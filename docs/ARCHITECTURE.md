# Architecture, telemetry contract, API and database

## 1. Components

| Layer | Implementation | Responsibility |
|---|---|---|
| Node | ESP32, `firmware/landslide_monitor` (4.0.0) | calibration, sampling, filtering, features, diagnostics, telemetry, offline queue, alert outputs |
| Backend | FastAPI + uvicorn, one process (`python -m backend.run`) | auth, validation, storage, eligibility, Random Forest inference, events, SSE, exports, static dashboard |
| Storage | SQLite (WAL), `backend/data/landslide.db` | devices, sessions, readings, predictions, events, annotations, training runs |
| ML | scikit-learn RandomForest in a Pipeline, `ml/artifacts/<version>/` | training, evaluation, registry, inference |
| Dashboard | `frontend/` (ES modules + Chart.js) | live monitoring, history, model/dataset, device status |

### Firmware tasks
* **Loop task (core 1):** MPU6050 at 50 Hz (complementary filter on the gravity vector, gyro bias removed),
  ADC at 10 Hz (4-sample average + EMA), 1-Hz snapshot (features, quality flags, independent per-sensor
  histories), a reading every 2 snapshots, non-blocking calibration state machine, LED/buzzer patterns
  (pure function of time), serial commands. Task watchdog 30 s.
* **SW-420 ISR:** counts raw transitions and filtered events (20 ms dead time) into monotonic counters; the
  loop converts them to per-second bins, so no event between uploads is lost.
* **Network task (core 0):** Wi-Fi reconnection with back-off (2–30 s), HTTP POST with exponential back-off
  (1–30 s), newest-first delivery (live) then oldest-first backlog (`replay: true`), response validation.
  The RAM queue holds 120 readings (~4 min); oldest are dropped and counted when full.

## 2. Telemetry contract (schema 2.0, features fs-1)

`POST /api/readings`, headers `X-Device-Id`, `X-Device-Token`, `Content-Type: application/json`, body ≤ 64 KiB.

```json
{
  "schema": "2.0", "feature_schema": "fs-1", "origin": "device",
  "device_id": "esp32-node-1", "fw": "4.0.0", "boot_id": "9f3a61c2",
  "session_id": "9f3a61c2-01", "reading_id": "9f3a61c2-01-000123", "seq": 123,
  "uptime_ms": 251004, "epoch_ms": null, "replay": false, "op_state": "MONITORING",
  "rssi": -61, "faults": [], "buffer": {"queued": 0, "dropped": 0},
  "cal": {"status": "OK", "trigger": "boot", "error": null, "attempts": 1, "completed_uptime_ms": 5310,
          "gravity0": [0.198, 0.012, 0.980], "gyro_bias_dps": [-1.2, 0.8, 0.3], "incl0_deg": 11.48,
          "accel_std_g": 0.0021, "gyro_std_dps": 0.061, "vib_bg_eps": 0.0, "soil_raw0": 3010, "water_raw0": 0,
          "prev_incl0_deg": 11.52, "prev_shift_deg": 0.09, "nvs_saved": true, "mount_axis": "+Z", "samples": 200},
  "soil":  {"raw": 3002, "filt": 3001.4, "do": 1, "moisture": 0.3435, "pct": 34.35, "rate_per_min": 0.0003,
            "mean_30s": 0.3431, "std_30s": 0.0009, "health": "OK", "flags": []},
  "water": {"raw": 0, "filt": 0.0, "wetness": 0.0, "pct": 0.0, "wet_s": 0.0, "rate_per_min": 0.0,
            "mean_30s": 0.0, "health": "OK", "flags": ["AT_DRY_ENDPOINT"]},
  "mpu":   {"ax": 0.198, "ay": 0.012, "az": 0.980, "gx": 0.02, "gy": -0.01, "gz": 0.0, "incl0_deg": 11.48,
            "incl_deg": 11.50, "tilt_deg": 0.031, "incl_change_deg": 0.02, "roll_deg": 0.7, "pitch_deg": -11.4,
            "tilt_rate_dpm": 0.004, "acc_mag_g": 1.0003, "acc_dyn_g": 0.0021, "gyro_mag_dps": 0.071,
            "temp_c": 27.4, "health": "OK", "flags": []},
  "vib":   {"do": 0, "transitions": 0, "events": 0, "window_ms": 2001, "eps": 0.0, "bg_eps": 0.0,
            "above_bg_eps": 0.0, "ev_5s": 0, "ev_10s": 0, "ev_30s": 0, "trend_eps": 0.0, "health": "OK"}
}
```
(illustrative values)

Rules: unavailable values are `null` (never 0, never NaN/Infinity — the backend rejects NaN with HTTP 400);
validation ranges are physical limits (`backend/schemas.py`), e.g. tilt rate ±20,000 °/min; unknown extra
fields are ignored for validation but kept in the stored original payload; `reading_id` is unique and
retransmissions keep their original id/session (duplicates return `"status": "duplicate"` with the stored
prediction).

Health values: `OK`, `SUSPECT` (classified, `data_quality: DEGRADED`), `FAULT` (no prediction),
`NOT_CALIBRATED`. Sensor flags and device faults are listed in `firmware/landslide_monitor/ls_logic.h`.

### Feature schema fs-1 (`ml/feature_schema.json`)
| Feature | From | Unit |
|---|---|---|
| soil_moisture, soil_rate_per_min, soil_std_30s | soil.moisture / rate_per_min (30-s LSQ slope) / std_30s | fraction, /min, fraction |
| water_wetness, water_wet_duration_s, water_mean_30s | water.wetness / wet_s (continuous ≥ 0.10) / mean_30s | fraction, s, fraction |
| tilt_deg, tilt_rate_deg_per_min | mpu.tilt_deg (angle between current and baseline gravity vectors) / 10-s LSQ slope | °, °/min |
| accel_dyn_g, gyro_mag_dps | std of ‖a‖ over the 2-s interval / RMS bias-corrected angular rate | g, °/s |
| vib_events_per_s, vib_above_bg_per_s, vib_events_30s | filtered SW-420 events over the interval / above calibration background / last 30 s | events/s, events |

## 3. Ingestion workflow
1. Authenticate device (constant-time token compare) → 401; payload device must match → 403.
2. Parse JSON (NaN rejected → 400) and validate schema 2.x → 422 with field locations (logged and stored as a `TELEMETRY_REJECTED` event, rate-limited).
3. Duplicate check by `reading_id`.
4. Transaction 1: upsert device, create session on first sight (`SESSION_STARTED`), store reading (flat columns + original payload), calibration transition events.
5. Live check: `replay=true` or `seq ≤ last seq of the same boot` ⇒ stored and classified, but never replaces the current state or triggers alerts.
6. Eligibility (`backend/services/eligibility.py`): CALIBRATING → SENSOR_FAULT (calibration failed / sensor FAULT / no MPU baseline / out-of-range feature) → MODEL_NOT_READY (schema mismatch, no allowed model) → INSUFFICIENT_DATA (any feature null) → OK.
7. Random Forest inference (model loaded once at startup).
8. Transaction 2: store prediction (status, class, probabilities, model version, provenance, latency); live transition events (`RISK_CHANGED`, `PREDICTION_AVAILABLE/UNAVAILABLE`, `FAULT_RAISED/CLEARED`, `DEVICE_ONLINE`). A failure here keeps the reading and answers `INFERENCE_ERROR`.
9. Response to the ESP32; SSE broadcast of the same reading + prediction.

### Device response
```json
{"status": "success", "device_id": "esp32-node-1", "session_id": "9f3a61c2-01",
 "reading_id": "9f3a61c2-01-000123", "is_live": true, "prediction_available": true,
 "prediction_status": "OK", "risk_state": "WATCH", "confidence": 0.91,
 "probabilities": {"SAFE": 0.06, "WATCH": 0.91, "WARNING": 0.03, "CRITICAL": 0.0},
 "model_version": "rf-…", "model_provenance": "experimental", "prediction_source": "random_forest",
 "data_quality": "OK", "predicted_at": "2026-11-26T13:43:33.255Z", "server_time": "…", "reason": null}
```
Without a prediction: `prediction_available: false`, `risk_state: null`, `prediction_status` one of
`CALIBRATING | SENSOR_FAULT | MODEL_NOT_READY | INSUFFICIENT_DATA | INFERENCE_ERROR` and a `reason`.
The firmware accepts a prediction only if device, session and reading id match, the class is one of the four,
a model version is present and the reading is ≤ 10 s old.

## 4. HTTP API
| Method & path | Purpose | Auth |
|---|---|---|
| POST `/api/readings` | telemetry ingestion | device token |
| GET `/api/readings/latest?device_id=` | latest live reading + prediction | – |
| GET `/api/predictions/latest?device_id=` | latest live prediction (with `stale` flag) | – |
| GET `/api/readings/history?device_id&session_id&start&end&risk_state&origin&limit&before&with_total` | keyset-paginated readings (newest first) | – |
| GET `/api/readings/series?…&max_points=` | chart series, time-bucket averages for long ranges (NULLs preserved) | – |
| GET `/api/sessions`, `/api/sessions/{id}` | sessions, calibration details, prediction summary, labels | – |
| GET `/api/devices` | devices, online state | – |
| GET `/api/events?kind&severity&category=communication&…` | events / alerts | – |
| GET `/api/model/status`, `/api/model/metrics` | active model, provenance, stored evaluation | – |
| POST `/api/model/reload` | reload registry's active model | admin token |
| GET `/api/dataset/summary`, `/api/dataset/labels` | labelled/unlabelled/ambiguous counts, annotations | – |
| POST `/api/dataset/labels`, DELETE `/api/dataset/labels/{id}` | experiment annotations | admin token |
| GET `/api/export/csv?scope=readings|labeled&…` | streamed CSV export | – |
| GET `/api/stream` | Server-Sent Events: `snapshot`, `reading`, `event`, `model`, `heartbeat` (5 s) | – |
| GET `/api/health` | liveness, DB, model readiness, config | – |

Interactive docs: `http://localhost:8000/docs`. Times accept ISO-8601 or epoch seconds; responses use UTC ISO-8601 with `Z`.

## 5. Database (migrations in `backend/migrations.py`)
| Table | Key content |
|---|---|
| `schema_migrations` | applied versions |
| `devices` | last seen, firmware, boot/session, last seq, RSSI, last live reading |
| `sessions` | device, boot, first/last seen, calibration status/trigger/error/JSON, reading count |
| `readings` | unique `reading_id`, ids, server UTC time (authoritative), uptime, replay/live flags, origin, op state, faults, raw ADC, health, 13 feature columns, quality flags, original payload |
| `predictions` | 1:1 with readings: status, class, confidence, 4 probabilities, model version + provenance, data quality, reason, latency |
| `events` | sessions, calibrations, risk transitions, faults, connectivity, rejections, model changes, annotations |
| `annotations` | device, optional session, time range, label, notes, author |
| `training_runs` | model version, provenance, dataset hash, size, status, summary metrics |
| `legacy_*_v1` | preserved tables of the previous system |

Indexes on time, device+time, session+seq, event time. Before any migration of a non-empty database a
consistent copy is written to `backend/data/backups/`. Manual backup: `python -m backend.manage backup`.
No automatic data deletion is implemented (retention is a manual decision).

## 6. Security
Per-device tokens (`LANDSLIDE_DEVICE_TOKENS`), admin token for annotations and model reload, strict input
validation, parameterised SQL only, 64 KiB body limit, CORS disabled unless configured, CSP and other
security headers on the dashboard, generic error messages (details only in logs), security-relevant log
lines (`logs/backend.log`, rotated 5 × 5 MB), model artifacts loaded only from `ml/artifacts` after a
SHA-256 check. Limitation: plain HTTP — tokens and data are readable by anyone on the same Wi-Fi. Use an
isolated hotspot; for stronger protection put the backend behind a TLS reverse proxy (e.g. Caddy) and use
`WiFiClientSecure` on the ESP32 (not implemented).
