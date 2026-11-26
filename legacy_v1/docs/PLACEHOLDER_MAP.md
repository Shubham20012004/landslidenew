# Report placeholder → what fills it

Fill a placeholder only from the source named here. Rows marked **needs real prototype runs** must not be
filled from simulated or synthetic data. Run the report tools without `--allow-simulated`.

| Report location | Placeholder | Filled by | Source type |
|---|---|---|---|
| Sec. 5.1 | [INSERT BOARD MODEL] | Read the module marking; firmware prints chip model + PSRAM at boot / `status` | Hardware inspection |
| Sec. 5.2 | [VERIFY: polarity] | `cal` wizard prints "reading falls/rises when wetter" | Real calibration |
| Sec. 5.5 | [INSERT RESISTOR VALUES] | Read resistor colour codes / multimeter | Hardware inspection |
| Sec. 5.5, 5.6, Table 6.1 | [INSERT FROM FIRMWARE] LED/buzzer mapping | README §"Local alerts", `alertsUpdate()` in the .ino | Firmware |
| Sec. 5.6 | [VERIFY: circuit] buzzer driver | Your circuit (transistor or direct) | Hardware inspection |
| Sec. 5.7 | [VERIFY: actual supply arrangement] | Your circuit | Hardware inspection |
| Table 5.1 | GPIO, ADC1/ADC2, I²C address | `python report_tools/make_report.py` → `report_tools/outputs/table_5_1_gpio.tex` (generated from the .ino) + `scan` output for 0x68/0x69 | Firmware + real `scan` |
| Table 5.2 | [VERIFY module] | Module part numbers | Hardware inspection |
| Sec. 6.1 | [INSERT SAMPLING INTERVAL] | `SAMPLE_INTERVAL_MS` in .ino | Firmware config (decide; see OPEN_QUESTIONS) |
| Sec. 6.2 / Table B.3 | [INSERT CALIBRATION VALUES], [DATA] raw readings | `cal` wizard output and `status` → "Calibration" lines | **Real calibration** |
| Sec. 6.3 | r_th [INSERT], reset/decay [VERIFY] | `RAIN_WET_THRESHOLD`; Eq. 6.2 reset implemented | Firmware config |
| Sec. 6.4 | [VERIFY: tilt formulation] | docs/REPORT_EXTRACT.md (complementary filter, Eq. 6.4 on filtered gravity vector) | Firmware |
| Sec. 6.5 | N = [INSERT] | `MA_WINDOW_N` | Firmware config |
| Sec. 6.6 | Calibration conditions and N_c | `BASELINE_SAMPLES`, your baseline pose | Firmware config + real procedure |
| Sec. 6.7 | L = [INSERT] | `RATE_WINDOW_L` (rates per minute) | Firmware config |
| Fig. 6.1, Eq. 6.9 | thresholds, β [INSERT FROM FIRMWARE] | CONFIG block (`M_LO` … `BETA_T`) | Firmware config (provisional until tuned) |
| Eq. 6.11 | τ1, τ2, τ3; hysteresis [VERIFY] | `TAU1..3`, `HYSTERESIS`, `MIN_DWELL_MS` | Firmware config |
| Sec. 7.2 | [INSERT VERSION AND DATE RANGE] | `python historical/analyze_glc.py --input <GLC.csv>` → `summary.json` (`date_range`, `input_file`, `analysis_date`) | Downloaded NASA GLC |
| Table 7.2 | [RE-VERIFY] shares, Other [INSERT] | `summary.json` → `trigger_share_percent`, `report_trigger_shares_recheck` | Downloaded NASA GLC |
| Fig. (Sec. 7.4) | [INSERT TRIGGER DISTRIBUTION BAR CHART] | `historical/outputs/trigger_distribution.png` | Downloaded NASA GLC |
| Sec. 7.5–7.7 | [INSERT RE-ANALYSIS RESULT] | `top_countries.png`, `monthly_seasonality.png`, `fatalities` block / `fatalities_hist.png` | Downloaded NASA GLC |
| Sec. 8.2 | [INSERT ACTUAL DATASET SIZE AND CLASS DISTRIBUTION] | If the report uses the synthetic set: `ml/outputs/dataset_profile.json`, **stated as synthetic**. For a real dataset: export real runs | Synthetic (labelled) or real runs |
| Sec. 8.3 | [VERIFY: final feature list] | `ml/outputs/metrics.json` → `features`, `unit_conversion` | Training script |
| Sec. 8.5 | [INSERT] cleaning | `ml/train_model.py` docstring + `dropna` on required columns; firmware excludes faulty samples | Training script |
| Sec. 8.6 | [INSERT SPLIT STRATEGY AND RATIO] | `metrics.json` → `split` (5-fold StratifiedGroupKFold by experiment, 10/50 experiments held out) | Training script |
| Table 8.2 | hyperparameters [INSERT] | `metrics.json` → `hyperparameter_search.best_params` + `class_weight`, `random_state` | Training script |
| Sec. 8.11 | [VERIFY: deployment form] | Backend loads `ml/outputs/rf_model.joblib`, advisory only | Software |
| Sec. 8.12, 11.5 | accuracy/macro metrics [INSERT] | `metrics.json` → `test_metrics_grouped_holdout`, **labelled SYNTHETIC** | Synthetic (labelled) |
| Sec. 8.13, 11.5 | [INSERT FEATURE IMPORTANCE PLOT] | `ml/outputs/feature_importance.png`, `permutation_importance.png` | Synthetic (labelled) |
| Table 8.3, 11.5 | confusion matrix [DATA] | `ml/outputs/confusion_matrix.png` / `metrics.json`, **labelled SYNTHETIC** | Synthetic (labelled) |
| Sec. 8.16 | [INSERT: MANUAL / RULE-ENGINE / OTHER] | **Unknown for the synthetic CSV**: ask whoever generated it | Open question |
| Sec. 8.17.3 | [VERIFY: select policy] | Rule-authoritative + disagreement flag | Software |
| Table 9.1 | field names | `backend/models.py` (`Reading`) | Software |
| Sec. 9.1 | [VERIFY: protocol and interval] | HTTP JSON POST, `TELEMETRY_INTERVAL_MS` | Firmware config |
| Sec. 9.7 | graph windows, sampling [INSERT] | Dashboard window selector 5/15/60 min/24 h, poll every 2 s | Software |
| Sec. 9.9 | [VERIFY: remote notifications] | Optional Telegram, disabled by default | Software |
| Sec. 9.10 | [VERIFY: buffering behaviour] | 360-sample ring buffer, dropped count | Firmware |
| Sec. 10.1–10.3 | slope dimensions, soil, angle, placement, water volumes [INSERT] | Measure and log in Table B.2 | **Needs real prototype runs** |
| Table 10.2 | [DATA] per scenario | `report_tools/outputs/table_10_2_summary.tex` from real runs | **Needs real prototype runs** |
| Table 10.3 | [DATA] transitions | `report_tools/outputs/table_10_3_transitions.tex` | **Needs real prototype runs** |
| Sec. 10.5 | runs per scenario [INSERT] | Count of experiments in `/api/experiments` | **Needs real prototype runs** |
| Sec. 10.7, 11.2 | response delay, noise [INSERT FROM EXPERIMENTAL LOG] | `report_tools/outputs/ts_*.png` + `raw` noise | **Needs real prototype runs** |
| Sec. 11.1 | ADC noise, MPU offset/drift [INSERT MEASUREMENT OR STATE NOT MEASURED] | `raw` (30-sample std dev), `baseline` output (gyro bias); long static log | **Needs real measurement** |
| Sec. 11.2 | [INSERT SOIL/TILT/RAIN VS TIME PLOT] | `ts_*.png` (real device only) | **Needs real prototype runs** |
| Sec. 11.3 | [INSERT RISK SCORE VS TIME PLOT WITH STATE THRESHOLDS], flicker | `ts_*.png` lower panel; count STATE_CHANGE events | **Needs real prototype runs** |
| Sec. 11.4 | latency, data loss [INSERT MEASUREMENT] | `latency.json`/`latency.tex` (needs NTP on the ESP32), `dropped_count`. Sampling-to-LED latency (NFR-2) needs a separate bench measurement (e.g. phone slow-motion video) | **Needs real measurement** |
| Figs. 5.2, 9.3, 10.2 | photographs/screenshots | Your photos; dashboard screenshot with "Real device only" | **Needs real prototype** |
| References | [INSERT ACCESS DATE] | Date you accessed each web source | Manual |
