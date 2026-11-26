# Random Forest: data, training, evaluation, activation

## 1. Dataset inventory (inspected 2026-11-26)

| Source | Rows | Features | Labels | Usable for the four-sensor model? |
|---|---:|---|---|---|
| Real readings, old firmware 1.0.0–3.1.1 (now `legacy_readings_v1`) | 2,204 | moisture, digital rain, tilt, rates, old risk score | none (only rule-engine states) | **No** — no analog water, vibration or accelerometer-dynamics features; rule-engine states must not become labels |
| `datasets/synthetic/legacy_v1/…5000.csv` | 5,000 (50 sessions) | 5 (moisture/rain index, tilt, two rates) | origin undocumented | **No** — incompatible feature set |
| Real readings, firmware 4.x | 0 so far | fs-1 (13) | experiment annotations | Yes, once collected and labelled |
| `datasets/synthetic/four_sensor_demo_v1.csv` | 36,033 (180 sessions) | fs-1 | generator script stages | Demonstration only |
| NASA GLC | not bundled | event attributes | event records | No (no sensor measurements) |

**Deficiency:** there is currently no independently labelled real four-sensor data, so no model with
real-world meaning can be trained yet. The pipeline, data collection and labelling workflow are complete.

## 2. Labelling real experiments
Dashboard → *Model & dataset* → *Experimental data labelling* (admin token from `.env`):
choose the device, optionally a session (fills its time range), the start/end and the condition **you
observed and documented** during the run. Rules enforced in code:
* only `origin = 'device'` readings in normal monitoring with a valid calibration, no FAULT sensor and all 13
  features present are training-eligible;
* a reading covered by conflicting labels is *ambiguous* and excluded;
* model predictions are never used as labels; simulated readings never enter the dataset.

## 3. Training (`train.bat` / `python -m ml.train`)
1. Load (experimental: labelled readings from SQLite via the same SQL as the labelled CSV export; synthetic: CSV).
2. Schema validation (required columns, numeric types).
3. Cleaning without imputation: drop rows with missing or non-finite features, out-of-range values,
   unknown labels and exact duplicates; counts are reported.
4. Sufficiency check (experimental default: ≥ 5 sessions, every class ≥ 30 readings in ≥ 2 sessions).
5. Class-imbalance ratio; `class_weight="balanced_subsample"`.
6. Hold-out: `StratifiedGroupKFold(5, shuffle, seed)` by **session**; a fold containing all classes is held out.
   No session appears in both sets (asserted) — this also prevents leakage from overlapping rolling windows.
7. Grouped 4-fold CV on training sessions only, small grid (`max_depth ∈ {None, 14}`, `min_samples_leaf ∈ {1, 5}`,
   200 trees, `max_features="sqrt"`), scoring macro-F1.
8. Refit best model on all training sessions; evaluate once on held-out sessions.
9. Save `model.joblib` (Pipeline: `ColumnTransformer` that selects the 13 features in schema order → RF),
   `metadata.json` (provenance, dataset hash/size/classes, features, hyperparameters, environment, model
   SHA-256), `metrics.json`, `confusion_matrix.png`, `feature_importance.png`; record a training run in the DB.
10. Activation only if: integrity check passes, the bundle loads with the backend's feature schema and
    classes, a held-out row can be predicted, and macro-F1 beats the majority-class baseline by > 0.05.
    The previous model stays registered: `python -m ml.registry rollback`.

Inputs never include session/reading ids, timestamps or labels. Seeds are fixed (training seed 42,
generator seed 20261126); a test verifies identical results across runs.

## 4. Inference
Loaded once at backend start (`POST /api/model/reload` after activation). The backend only uses a model
whose provenance is allowed: `experimental` always, `synthetic_demo` only with `LANDSLIDE_DEMO_MODE=true`.
Otherwise every reading gets `MODEL_NOT_READY`. Single-row inference takes a few milliseconds (n_jobs=1).

## 5. Synthetic demonstration model (current active artifact)
`rf-synthetic-20261126-133932`, trained on `four_sensor_demo_v1.csv` (36,033 readings, 180 sessions;
SAFE 17,061 / WATCH 11,765 / WARNING 6,009 / CRITICAL 1,198). The generator (`ml/synthetic.py`) simulates
scripted scenarios (stable, disturbance, wetting, saturation creep, progressive failure, sudden slip,
sustained vibration), computes features exactly as the firmware does and labels each reading with the
scripted stage (with jittered boundaries and sensor noise).

Held-out results (36 unseen sessions, 7,413 readings) — **these measure recovery of the generator's
script, not real-world landslide prediction**:

| Metric | Value |
|---|---:|
| Accuracy | 0.966 |
| Balanced accuracy | 0.958 |
| Macro F1 | 0.962 (majority-class baseline 0.174) |
| Grouped CV macro F1 (training sessions) | 0.963 |

| Class | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| SAFE | 0.986 | 0.974 | 0.980 | 3,959 |
| WATCH | 0.923 | 0.972 | 0.947 | 2,161 |
| WARNING | 0.981 | 0.913 | 0.946 | 943 |
| CRITICAL | 0.977 | 0.971 | 0.974 | 350 |

Confusion matrix (rows true, columns predicted; SAFE, WATCH, WARNING, CRITICAL):
`[[3858, 101, 0, 0], [54, 2100, 7, 0], [0, 74, 861, 8], [0, 0, 10, 340]]`.
Most important features (permutation, held-out): tilt_deg, water_mean_30s, soil_moisture, vib_events_30s,
tilt_rate_deg_per_min. Regenerate with `train.bat synthetic`.

## 6. Limitations
Small real datasets will give unstable metrics; experimenter labels on a miniature slope are not landslide
outcomes; class imbalance (CRITICAL is rare); the RF classifies each reading independently and can alternate
between adjacent classes near a boundary; synthetic performance says nothing about real performance.
