# Datasets and provenance

Sources are kept separate and are never mixed without a justified mapping.

| Folder | Content | Use |
|---|---|---|
| `synthetic/four_sensor_demo_v1.csv` | **SYNTHETIC** four-sensor demonstration data from `python -m ml.synthetic` (seed 20261126, 180 sessions, 36,033 readings). Labels = the generator's scripted scenario stages. | Demonstration model only (`LANDSLIDE_DEMO_MODE=true`). Not evidence of real prediction accuracy. |
| `synthetic/legacy_v1/landslide_synthetic_experimental_dataset_5000.csv` | Older synthetic CSV (5 features: moisture, rain index, tilt, two rates; label origin undocumented). | **Not used.** Incompatible with the four-sensor system: no vibration, water duration or accelerometer features. Kept unchanged for reference. |
| *(database)* `backend/data/landslide.db` | Real ESP32 readings (`origin='device'`) + experiment annotations. | Experimental training data: `train.bat` / `python -m ml.train`. Labelled export: dashboard or `/api/export/csv?scope=labeled`. |
| *(not bundled)* NASA Global Landslide Catalog | Historical event records. | Context analysis only (`historical/analyze_glc.py`). No sensor measurements: never used to train the sensor model, and no sensor values are invented for it. |
| `../tests/fixtures/` | Hand-written test fixtures. | Automated tests only. |

Simulated telemetry (`scripts/simulate_device.py`, `origin='simulated'`) is stored for software testing
but excluded from experimental training data by SQL (`origin = 'device'`).
