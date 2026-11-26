# Synthetic dataset inspection and ML results

> **SYNTHETIC DATA.** Everything on this page comes from `landslide_synthetic_experimental_dataset_5000.csv`.
> It shows that the software pipeline works. It is not prototype data, not real-world validation,
> and must not fill the report's experimental [DATA] placeholders.

## 1. Inspection (done before choosing features)

File: `ml/data/landslide_synthetic_experimental_dataset_5000.csv` (unchanged copy, SHA-256 `cc34da28…bcd`).

| Column | Type | Range | Notes |
|---|---|---|---|
| `experiment_id` | string | EXP-001 … EXP-050 | 50 groups × 100 rows |
| `timestamp` | string | 2026-01-01 … | 60 s steps within each experiment |
| `moisture_index` | float | 0 – 100 | **0–100 scale** (report uses [0,1]) |
| `rain_index` | float | 0 – 100 | **0–100 scale** |
| `tilt_change_deg` | float | 0.066 – 5.951 | degrees, always positive |
| `moisture_rate_per_min` | float | −4.455 – 5.146 | percentage points per minute |
| `tilt_rate_deg_per_min` | float | −0.023 – 0.096 | degrees per minute |
| `risk_label` | string | 4 classes | origin undocumented |

No missing values. Class counts: **WATCH 2662, SAFE 1266, WARNING 770, CRITICAL 302**.
Experiments containing each class: SAFE 27, WATCH 33, WARNING 16, **CRITICAL 5**.
Label mix per experiment: SAFE/WATCH 17, SAFE 10, WARNING/WATCH 9, WATCH 7, CRITICAL/WARNING 5, WARNING 2.
There is **no rain-duration column** (report Table 8.1 lists one).

## 2. Features and unit conversion

| Model feature | From CSV | Conversion |
|---|---|---|
| `moisture` | `moisture_index` | ÷ 100 → [0,1] |
| `rain` | `rain_index` | ÷ 100 → [0,1] |
| `tilt_change_abs_deg` | `tilt_change_deg` | absolute value (degrees) |
| `moisture_rate_per_min` | `moisture_rate_per_min` | ÷ 100 → index per minute |
| `tilt_rate_deg_per_min` | `tilt_rate_deg_per_min` | unchanged |

The backend computes the same five features from device telemetry (`backend/ml_advisor.py`).

## 3. Split and search

* **Group split by `experiment_id`** with `StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=42)`.
  The first fold containing all four classes is the held-out test set: **10 experiments, 1000 rows**
  (EXP-002, 006, 010, 015, 027, 028, 030, 032, 040, 047). No experiment appears on both sides (asserted in code).
* Hyperparameter search: `GridSearchCV`, inner `GroupKFold(4)` on the training experiments, scoring macro-F1,
  `class_weight="balanced"`, grid `n_estimators ∈ {100, 300}`, `max_depth ∈ {None, 8, 16}`,
  `min_samples_leaf ∈ {1, 5}`, `max_features ∈ {sqrt, None}` (24 combinations).
* Best: `n_estimators=300, max_depth=None, min_samples_leaf=1, max_features="sqrt"` (inner CV macro-F1 0.863).

## 4. Results (SYNTHETIC, held-out experiments)

Produced by `python ml/train_model.py` on 2026-11-22 (Python 3.10.9, scikit-learn 1.5.2).

| Metric | Value |
|---|---|
| Accuracy | 0.919 |
| Macro precision / recall / F1 | 0.928 / 0.907 / 0.914 |
| Recall SAFE / WATCH / WARNING / CRITICAL | 0.789 / 0.990 / 0.912 / 0.939 |
| CRITICAL recall | 0.939 (62 of 66) |
| Errors by ordinal distance | 0 levels: 919 · 1 level: 81 · ≥ 2 levels: **0** |
| 5-fold grouped CV macro-F1 | 0.879 ± 0.083 |
| CRITICAL recall across folds (folds that contain CRITICAL) | 0.94, 0.54, 0.97, 0.89 (one fold had no CRITICAL) |

Confusion matrix (rows = true, columns = predicted; SAFE, WATCH, WARNING, CRITICAL):

```
SAFE      254   68    0    0
WATCH       6  572    0    0
WARNING     0    0   31    3
CRITICAL    0    0    4   62
```

**Why the numbers need care**
* Only **5 experiments** contain CRITICAL, so any test fold holds 0–2 of them. CRITICAL recall ranges from 0.54 to 0.97 between folds. One "0.94" figure is not a stable estimate.
* The test fold has only 34 WARNING samples.
* The label origin is unknown. If the labels came from rules, the forest is learning to reproduce those rules (report Sec. 8.16).
* **Leakage comparison only:** a random-row split gives macro-F1 0.969. That number is inflated because rows from the same experiment land in both train and test. Never cite it.

Feature importance (impurity): moisture 0.42, rain 0.37, tilt 0.15, tilt rate 0.05, moisture rate 0.01.
Permutation importance on the test set (drop in macro-F1): moisture 0.47, rain 0.47, tilt 0.26, rates ≈ 0.

Informational: the **provisional** firmware rule engine matches the CSV labels on 80.0 % of rows (macro-F1 0.821).
It labels 799 WATCH rows as SAFE. This only shows the provisional thresholds differ from whatever produced the labels (see OPEN_QUESTIONS B4).

Outputs: `ml/outputs/metrics.json`, `dataset_profile.json`, `rf_model.joblib`, and the PNG figures (all marked SYNTHETIC).

## 5. Real data later

When real runs exist: export `http://localhost:8000/api/export.csv?source=device`, then run
`python ml/evaluate_real.py --csv real.csv`. Without an independent label column, the script only reports
agreement with the rule engine, which is **not** validation.
