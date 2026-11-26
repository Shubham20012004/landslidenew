# Updating the project report (`docs/reference/main.pdf`)

The report exists only as a PDF (no LaTeX/Word source in the repository), so it could not be edited
directly. It describes the previous design (three sensors, on-device weighted score
`R = 0.40 M + 0.30 Rr + 0.30 T`, rule engine authoritative, ML advisory). Below is what each chapter must
change to describe the implemented system. Keep the existing valid references and the literature review.
Do not fill any experimental [DATA] placeholder from simulated or synthetic data.

| Chapter | Replace / add |
|---|---|
| 1.5 Proposed solution, 1.6 Objectives | Four sensors (soil moisture, analog water sensor, MPU6050, SW-420); Random Forest is the final classifier; ESP32 performs sensing, feature extraction and alert actuation only. |
| 3 Requirements | FR: automatic calibration on power-on and `demo`; combined telemetry; persistent logging; experiment labelling; RF inference; operational states separate from risk classes; stale-alert behaviour. |
| 4 Architecture (Fig. 4.x) | Use the flow in README §1. Remove the "risk assessment layer" on the node; the ML layer becomes authoritative. |
| 5 Hardware, Table 5.1 | GPIO table from README §4 / docs/HARDWARE.md; rain sensor → analog water sensor S on GPIO 34; SW-420 on GPIO 15; 4 LEDs (19/21/22/23) and 2 buzzers (12/13) with transistor drivers; strapping-pin notes. |
| 6 Sensor fusion and risk | Delete Eq. 6.8–6.11 (components, β-mix, weighted score, τ thresholds) and hysteresis. Keep normalisation (Eq. 6.1) with the fixed endpoints 4090/921 and 0/1725; replace tilt (Eq. 6.4) by the gravity-vector angle `Δθ = atan2(‖g₀ × g‖, g₀·g)` with a complementary filter; rates = least-squares slopes over 30 s (soil, water) and 10 s (tilt); vibration features from interrupt counts with 20 ms dead time and calibration background; list the 13 fs-1 features (docs/ARCHITECTURE.md §2). |
| 6.6 Baseline calibration | docs/CALIBRATION.md: 200 samples at 50 Hz, stability criteria, NVS handling, failure handling, baseline-lock limitation and `prev_shift_deg`. |
| 7 Historical | Unchanged in substance; state that GLC is never used to train the sensor model. |
| 8 ML | Dataset section: state that no labelled real four-sensor data exists yet and that the delivered model is synthetic (docs/ML.md §1, §5). Feature list, session-grouped split, grouped CV grid, metrics with the synthetic caveat, confusion matrix and permutation importance from `ml/artifacts/<version>/` (figures are watermarked by provenance in their titles). 8.16/8.17: replace "hybrid rule/ML" by "RF authoritative; operational states for ineligible data". |
| 9 IoT & dashboard | SSE live stream, four dashboard sections, API table and database tables (docs/ARCHITECTURE.md §4–5), offline buffer (120 readings, RAM, replay flag), device authentication. 9.10 "local alert during Wi-Fi failure": now the last WARNING/CRITICAL keeps alerting (blinking) when stale; no on-device classification. |
| 10–11 Experiments/results | Re-run with firmware 4.x; collect labelled sessions per docs/ML.md §2; report real metrics only after `train.bat` succeeds on real data. |
| 12 Limitations | README §11 and docs/ML.md §6. |

Figures available now: `ml/artifacts/rf-synthetic-20261126-133932/confusion_matrix.png` and
`feature_importance.png` — both **synthetic demonstration results**; label them as such if used.
