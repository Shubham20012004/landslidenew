# AI-Based IoT Landslide Monitoring and Early Warning System (prototype)

ESP32 four-sensor node · FastAPI + SQLite backend · Random Forest risk classifier · live web dashboard.

> **Experimental engineering prototype.** SAFE / WATCH / WARNING / CRITICAL are outputs of a Random Forest
> trained on the available data. They are **not** validated public-safety landslide warnings, and model
> confidence is the model's class probability, **not** the probability of a real landslide.
> No independently labelled real four-sensor data exists yet; the bundled model is a **synthetic
> demonstration model** and is only used in demonstration mode (see §7).

## 1. What it does

```text
ESP32 power-on / `demo` ──► automatic initialisation + calibration (new session)
   soil moisture (AO 35, DO 32) · water sensor S (34) · MPU6050 (I2C 25/26) · SW-420 (15, interrupt)
        │ sampling (MPU 50 Hz, ADC 10 Hz, vibration interrupts), filtering, feature extraction, diagnostics
        ▼
one combined, versioned JSON reading every 2 s  ──(Wi-Fi, HTTP, token)──►  POST /api/readings
                                                                             │ validate · authenticate · de-duplicate
                                                                             │ store raw reading (SQLite, WAL)
                                                                             │ eligibility check → Random Forest
                                                                             │ store prediction (traceable to reading + model)
        ◄──────────── prediction response (risk, confidence, session, reading, model) ──┤
LEDs + buzzers (only from a validated, fresh prediction)          Server-Sent Events ──► dashboard
```

* The ESP32 computes **no risk score**. The old 40/30/30 weighted formula was removed.
* The Random Forest is the only source of risk classes. Operational states (`CALIBRATING`, `SENSOR_FAULT`,
  `MODEL_NOT_READY`, `INSUFFICIENT_DATA`, connection loss, stale prediction) are separate and never shown as SAFE.
* All readings, predictions, sessions, calibrations, events and experiment labels are stored persistently.

## 2. Repository layout

| Path | Content |
|---|---|
| `firmware/landslide_monitor/` | `landslide_monitor.ino`, `ls_logic.h` (hardware-independent logic, host-tested), `node_types.h`, `platformio.ini`, `secrets.example.h` |
| `backend/` | FastAPI app: `main.py`, `config.py`, `schemas.py` (telemetry contract), `database.py` + `migrations.py`, `repositories.py`, `services/` (ingestion, eligibility, model, real-time hub), `api/` |
| `ml/` | `feature_schema.json` (shared contract), `features.py`, `train.py`, `evaluate.py`, `registry.py`, `predict.py`, `synthetic.py`, `artifacts/` |
| `frontend/` | Dashboard (HTML/CSS/ES modules, bundled Chart.js — works offline, no build step) |
| `datasets/` | Synthetic demonstration data and provenance notes (`datasets/README.md`) |
| `scripts/` | `configure.py` (tokens/.env), `simulate_device.py` (hardware-free simulated ESP32) |
| `historical/` | NASA GLC event-data analysis (kept separate from sensor data) |
| `tests/` | pytest suite + `firmware_host/` C++ tests of `ls_logic.h` |
| `docs/` | Architecture & API, hardware, calibration, ML, troubleshooting, verification, report update |
| `legacy_v1/` | Archived code of the previous rule-engine version (unused; see its README) |

## 3. Quick start (Windows)

```powershell
setup.bat
```
```powershell
start.bat demo
```
Then open **http://localhost:8000/** (the console also prints the LAN URL and the exact `API_HOST` for the ESP32).

* `setup.bat` creates `.venv`, installs dependencies, creates `.env` with generated device/admin tokens
  (existing values are never overwritten), inserts the device token into `firmware/landslide_monitor/secrets.h`
  if missing, migrates the database (backup first) and checks the model.
* `start.bat` = normal operation: only models trained on **real experimental** data produce predictions.
  `start.bat demo` = demonstration mode: the synthetic demonstration model may produce predictions; the
  dashboard labels every such prediction "Demo model · synthetic training data".
* First start: allow Python through Windows Firewall on **Private** networks (see `docs/TROUBLESHOOTING.md`).

Ubuntu/Linux: `./setup.sh`, `./start.sh` (or `./start.sh demo`), `./train.sh`.

**Without hardware:** in a second terminal run the simulated ESP32 (tagged *simulated*, excluded from training):
```powershell
.venv\Scripts\python -m scripts.simulate_device --scenario progressive_failure --speed 4
```

## 4. Hardware and wiring

| Component | GPIO | Notes |
|---|---:|---|
| Red / Blue / Yellow / Green LED | 19 / 21 / 22 / 23 | 220–330 Ω series resistors |
| Soil moisture AO / DO | 35 / 32 | AO on ADC1 (works with Wi-Fi); AO ≤ 3.3 V |
| Water sensor **S** (analog, ~0–1725 counts) | 34 | ADC1; single analog signal, no DO line |
| SW-420 DO | 15 | interrupt; **strapping pin** |
| MPU6050 SDA / SCL | 25 / 26 | address 0x68 or 0x69 auto-detected |
| Buzzer 1 / Buzzer 2 | 12 / 13 | via NPN transistors; GPIO 12 is a **strapping pin** |

Electrical safety, strapping-pin rules (GPIO 12 must be LOW at reset), buzzer driver circuit and power
notes: **[docs/HARDWARE.md](docs/HARDWARE.md)**.

### Fixed calibration endpoints
| Sensor | Dry | Wet | Normalisation |
|---|---:|---:|---|
| Soil moisture | 4090 | 921 | `clamp((4090 − raw) / 3169, 0, 1)` |
| Water exposure | 0 | 1725 | `clamp(raw / 1725, 0, 1)` (relative exposure index, **not** rainfall in mm/h) |

ADC 4095 on the soil channel is treated as valid dry-end saturation (moisture 0, flag
`ADC_DRY_SATURATION`), not as a fault; the comparator output, noise level and saturation duration
distinguish suspicious saturation and disconnection (details in `docs/CALIBRATION.md`).

## 5. Firmware

1. Arduino IDE: board package **esp32 by Espressif** (2.0.x or 3.x), board **ESP32 Dev Module**. No extra libraries.
   PlatformIO: `cd firmware/landslide_monitor` then `pio run -t upload`. Both builds are compile-verified (see §10).
2. `secrets.h` (git-ignored): Wi-Fi SSID/password, `API_HOST` = laptop LAN IP printed by `start.bat`,
   `DEVICE_TOKEN` = the `esp32-node-1` token in `.env` (setup inserts it).
3. Serial Monitor 115200 baud, line ending *Newline*.

**Startup:** power-on → GPIO/ADC init → MPU6050 detection → fixed soil/water endpoints → 200 MPU samples at
50 Hz while measuring SW-420 background → stability checks → gyro bias + gravity baseline locked → new
session ID → saved to NVS (previous baseline kept as `calprev`) → monitoring starts. Keep the slope model
still for ~5 s. Predictions follow once the 30-s feature windows fill.

| Command | Action |
|---|---|
| `demo` | Recalibrate everything and start a new session (no other commands needed) |
| `status` | Readings, calibration, health, latest ML result |
| `raw` | Raw ADC/digital/MPU values |
| `test` | I²C scan, LED and buzzer test |
| `wifi` | Network diagnostics |
| `json` | Latest combined telemetry payload |
| `send` | Queue a reading immediately |
| `help` | Command list |

**Alert outputs** (driven only by a validated backend prediction for the current session and reading):

| Indication | LEDs | Buzzers |
|---|---|---|
| SAFE | green | off |
| WATCH | blue | off |
| WARNING | yellow | buzzer 1 intermittent (0.3 s / 1.5 s) |
| CRITICAL | red | buzzer 2 rapid (0.1 s / 0.1 s) |
| Stale WARNING/CRITICAL (no fresh prediction > 10 s) | that LED blinks 1 Hz | alert continues (never silently downgraded) |
| Calibrating | chase green→blue→yellow→red | off |
| Sensor fault / calibration failed | all LEDs short flash every 1 s | 60 ms chirp every 10 s |
| Backend/Wi-Fi unreachable | blue double flash every 2 s | off |
| Model not ready / insufficient data / stale SAFE-WATCH | blue + yellow short flash every 2 s | off |

Green is never shown without a fresh SAFE prediction.

## 6. Dashboard

Four sections: **Live monitoring** (status bar, risk panel with class probabilities, four sensor cards,
four live charts with 5 min–24 h ranges, recent events), **History & data logs** (filters, charts,
paginated readings + predictions, events, CSV export), **Model & dataset** (active model, held-out
metrics, confusion matrix, feature importance, feature schema, experiment labelling, labelled export,
reload), **Device & system** (devices, uptime, boot/session, RSSI, calibration results, sensor health,
faults, backend status, communication events). Updates arrive over Server-Sent Events with automatic
reconnection and resynchronisation; data older than 10 s is marked stale.

## 7. Machine learning

* 13 features (`ml/feature_schema.json`, version `fs-1`) computed on the ESP32; identical names in
  telemetry, database, training and inference. Missing features are never filled: the reading gets
  `INSUFFICIENT_DATA`.
* `train.bat` trains on **real labelled readings** (dashboard → Model & dataset → label experiment time
  ranges). It refuses to train until every class has ≥ 30 readings in ≥ 2 sessions and ≥ 5 sessions exist.
  Split by session (no session in both train and test), grouped CV on training sessions only, artifacts
  versioned with SHA-256 integrity, activation only after validation, rollback kept.
* **Current state:** the database contains **no labelled four-sensor readings** (the 2,204 earlier
  readings come from the old firmware, have no vibration/analog-water features and no labels; they are
  preserved as `legacy_readings_v1`). The active model `rf-synthetic-20261126-133932` is trained on
  synthetic demonstration data: held-out macro-F1 0.962 — this only measures how well it recovers the
  generator's scripted stages, **not** real-world accuracy.

Details: **[docs/ML.md](docs/ML.md)**.

## 8. Data collection workflow

1. `start.bat` (normal mode), power the ESP32, wait for "Calibrated".
2. Perform a documented scenario (stable, wetting, water exposure, controlled tilt, vibration, combined, faults).
3. Dashboard → Model & dataset → label the time range (or session) with the condition you observed, add notes.
4. Repeat for several sessions per class, then `train.bat`. Export: History → *Export CSV* or *Export labelled training data*.

## 9. Documentation

[Architecture, telemetry & API](docs/ARCHITECTURE.md) · [Hardware](docs/HARDWARE.md) ·
[Calibration](docs/CALIBRATION.md) · [Machine learning](docs/ML.md) ·
[Troubleshooting](docs/TROUBLESHOOTING.md) · [Verification record](docs/VERIFICATION.md) ·
[Report update](docs/REPORT_UPDATE.md) · [Datasets](datasets/README.md)

## 10. Verification status (summary)

| Item | Result |
|---|---|
| Firmware compile, PlatformIO `espressif32@6.9.0` (Arduino-ESP32 2.0.17), with and without `secrets.h` | PASS, 0 warnings (`-Wall`) |
| Firmware compile, arduino-cli 1.6.0 + `esp32:esp32` **3.3.13** (your Arduino IDE core), ESP32 Dev Module, `--warnings all` | PASS, 0 warnings |
| Firmware logic host tests (`ls_logic.h`, g++) | PASS (17,255 checks) |
| Backend + ML + stream tests (`pytest`) | 63 passed |
| Simulated end-to-end run (simulator → backend → RF → response → dashboard) | PASS |
| Dashboard in browser (desktop + 375 px mobile) | Verified, see docs/VERIFICATION.md |
| Real hardware (sensors, calibration, Wi-Fi, LEDs/buzzers) | **Not verified — no device was available** |

## 11. Limitations

Miniature model ≠ real slope; SW-420 pulse counts are not vibration amplitude or frequency; MPU6050
orientation change is not ground displacement; startup recalibration hides movement that happened while
the device was off (the angle to the previous saved baseline is reported as `prev_shift_deg`); the
offline buffer (~4 min) is RAM-only and lost on power loss; plain HTTP on the local network; the RF output
may alternate between adjacent classes near a boundary (no hidden smoothing rule is applied); no real
labelled data yet.
