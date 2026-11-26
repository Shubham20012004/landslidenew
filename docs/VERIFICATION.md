# Verification record (2026-11-26)

Environment: Windows 11, Python 3.10.9 (`.venv`), PlatformIO Core 6.2.0, MinGW g++ 6.3.0.
**No ESP32, sensors, LEDs or buzzers were connected.** Nothing below demonstrates that the hardware works.

## Firmware
| Check | Command | Result |
|---|---|---|
| Compile with `secrets.h` (Wi-Fi path) — `espressif32@6.9.0`, Arduino-ESP32 2.0.17, `esp32dev`, `-Wall` | `pio run` | SUCCESS, 0 warnings; RAM 80,536 B (24.6 %), flash 832,457 B (63.5 %) |
| Compile without `secrets.h` (copy of the sketch) | `pio run` | SUCCESS, 0 warnings; RAM 52,836 B, flash 333,273 B |
| Compile with `secrets.h` on **Arduino-ESP32 3.3.13** (the core installed for your Arduino IDE), arduino-cli 1.6.0 (official release), FQBN `esp32:esp32:esp32`, `--warnings all` | `arduino-cli compile` | SUCCESS, 0 warnings; program 1,024,160 B (78 %), globals 84,384 B (25 %). The first 3.x build showed two C++20 `-Wvolatile` warnings in the SW-420 ISR; fixed. Without `secrets.h`: SUCCESS, 0 warnings (program 428,457 B, globals 60,812 B) |
| Arduino auto-prototype problem with custom structs (`Vector3` in v3.1.1) | — | Fixed: all types live in `node_types.h` / `ls_logic.h` (first build attempt reproduced the error; resolved) |
| Host tests of `ls_logic.h` (normalisation, saturation classes, water quality, tilt geometry incl. ±179° roll wrap, complementary filter, regression windows, JSON writer never emits NaN, response parser incl. nested keys, prediction validation (device/session/reading/class/model/staleness), all LED/buzzer mappings, stale CRITICAL never downgraded, CRC32) | `tests/test_firmware_logic.py` | PASS, 17,255 checks |

## Automated tests
`python -m pytest` → **63 passed** (3 third-party deprecation warnings).

| File | Covers |
|---|---|
| test_ingestion.py | health; valid reading + full device response; SAFE/WATCH/WARNING/CRITICAL mappings (test-fixture model); auth 401/403 + logged rejection; 422 with field details (schema 3.0, out-of-range, missing sensor, bad id); 4,500 °/min tilt rate accepted; NaN rejected; duplicates; out-of-order and replayed readings stored but not live; new boot; INSUFFICIENT_DATA; CALIBRATING; SENSOR_FAULT; calibration failure; SUSPECT → DEGRADED; ADC 4095 still valid; feature-schema mismatch; MODEL_NOT_READY by provenance policy; prediction-store failure keeps reading; session/calibration/risk/fault events; offline/online detection; 413 body limit; dashboard + security headers; admin reload |
| test_queries.py | keyset pagination and filters; series keeps NULL gaps; bucketing; CSV export; sessions; annotation workflow (auth, summary, labelled export, ambiguity, delete); validation; simulated data never labelled; metrics read from artifact |
| test_ml.py | missing features stay None; cleaning counts; insufficiency report; no session leakage; identifiers not inputs; metrics consistent with confusion matrix and above baseline; reproducibility (identical metrics and probabilities); refusal on insufficient real data; registry activation/rollback/tamper detection/path escape; feature-order mismatch refused; inference needs all features; synthetic generator reproducible |
| test_database.py | fresh migration (WAL, foreign keys), idempotence; legacy tables preserved with backup; rollback on error; manual backup |
| test_realtime.py | SSE formatting rejects NaN; hub fan-out, slow-subscriber drop, cross-thread publish; **real uvicorn server**: device POST → stream delivers snapshot, the same reading/prediction and events; resynchronisation snapshot on reconnect |
| test_firmware_logic.py | host suite above; GPIO/endpoint constants; no risk formula or manual calibration commands; firmware JSON emits every fs-1 feature path |
| test_historical.py | GLC script on the 8-row test fixture |

## Integration (manual, simulated device)
Backend on a scratch copy of the real database + `scripts/simulate_device.py --scenario progressive_failure
--speed 8 --offline-at 100 --offline-for 30` (demo mode): CALIBRATING → INSUFFICIENT_DATA → SAFE → WATCH →
WARNING → CRITICAL; 277 live and 15 replayed readings stored, replays not live, events recorded, CSV export OK.
Inference latency after fixing thread fan-out: mean 12 ms, max 63 ms over 831 predictions (was 64 ms mean).

## Database migration of the real data
`setup.bat` on `backend/data/landslide.db`: backup `backend/data/backups/landslide-20261126T135715Z-pre-migration.db`
written first; migrations 1–2 applied; 2,204 legacy readings and 131 events preserved in `legacy_*_v1`.

## Dashboard (in-app browser, simulated device)
Live, History, Model & dataset and Device & system pages loaded from the backend; live updates via SSE;
charts with gaps; probabilities; demo/simulated banners; 375 px mobile layout without horizontal overflow.
Issues found and fixed during this check: chart tick callback ran before the chart existed (blocked
start-up), stale cached modules (now `Cache-Control: no-cache`), CSP blocked inline bar widths (inline styles
allowed, scripts still `'self'` only), clipped risk metadata, wrapped table cells, device-page stream status
not refreshing.

## Not verified
Real sensors, wiring, ADC behaviour, calibration on a physical model, Wi-Fi link to the ESP32, LED/buzzer
behaviour, NVS persistence on hardware, watchdog behaviour, offline buffer on hardware,
Ubuntu scripts (syntax-checked with `bash -n` only), real-data model performance (no labelled real data).
