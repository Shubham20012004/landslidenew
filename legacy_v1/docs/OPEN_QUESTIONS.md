# Open questions and provisional values

Every value below is **missing from, or in conflict with, the report** (`main.pdf`). The software uses a
clearly labelled PROVISIONAL value only so that it runs. All of them live in one place: the
`//@@BEGIN_CONFIG` block at the top of `firmware/landslide_monitor/landslide_monitor.ino`, and the
backend, simulator, dashboard and report tools read them from there. Changing a value there changes it everywhere.

**None of these values is a measured or validated result.** Decide each one, record why, and update the
report and this file.

## A. Missing from the report (marked [INSERT] or [VERIFY])

| # | Item | Report status | Provisional value in .ino | How to decide |
|---|---|---|---|---|
| 1 | Sampling period Δt | [INSERT SAMPLING INTERVAL] | `SAMPLE_INTERVAL_MS = 1000` | Keep 1 s unless the experiments need faster response; record it in Sec. 6.1. |
| 2 | Moving-average length N | [INSERT] | `MA_WINDOW_N = 10` | Look at `raw` noise; larger N = smoother but ≈(N−1)Δt/2 delay. |
| 3 | Rate window L | [INSERT] | `RATE_WINDOW_L = 30` samples (30 s) | Trade-off between smoothness and delay of ṁ, θ̇. |
| 4 | Calibration values m_dry, m_wet (and rain) | [INSERT CALIBRATION VALUES] | **none**: status starts UNVERIFIED; the old placeholders 3100/1300 and 3600/1200 are printed as examples only and never used | Run the `cal` wizard with real dry/wet references; copy the saved numbers into report Table B.3. |
| 5 | Baseline samples N_c and conditions | [INSERT] | `BASELINE_SAMPLES = 200` at 50 Hz (~4 s), still/level check | Record the pose and conditions used in the `baseline` step. |
| 6 | Rain wet threshold r_th | [INSERT] | `RAIN_WET_THRESHOLD = 0.30` | Choose after rain calibration (e.g. the index reached by a few drops). |
| 7 | Rain duration: reset or decay when dry | [VERIFY] | Reset to 0 (as Eq. 6.2 is written) | Confirm intended behaviour. |
| 8 | Component thresholds x_lo/x_hi | [INSERT FROM FIRMWARE] | M 0.40/0.90; ṁ 0/0.03 per min; r 0.20/0.90; d_r 0/600 s; \|Δθ\| 0.5/4.0°; θ̇ 0.01/0.08 °/min | Tune on the real slope model **before** recording final runs; do not tune on the runs you report. |
| 9 | Level/trend mix β | [INSERT FROM FIRMWARE] | `BETA_M = BETA_R = BETA_T = 0.30` | As above. |
| 10 | State thresholds τ1, τ2, τ3 | [INSERT] | 0.25 / 0.50 / 0.75 | As above. |
| 11 | Hysteresis / overrides | [VERIFY: rule engine details] | `HYSTERESIS = 0.03` on R, `MIN_DWELL_MS = 5000` for de-escalation; escalation immediate; no trend overrides | Confirm; report flicker with/without hysteresis (Sec. 11.3). |
| 12 | Tilt formulation | [VERIFY: tilt formulation] | Eq. 6.4 inclination on a complementary-filtered gravity vector (K = 0.98, gyro bias removed); risk uses \|Δθ\| | Confirm. See note C1. |
| 13 | Telemetry protocol and interval | [VERIFY: protocol and interval] | HTTP POST JSON to `/api/readings` every 2 s (`TELEMETRY_INTERVAL_MS`), batches of ≤ 10 | Confirm. |
| 14 | Offline buffering | [VERIFY: buffering behaviour] | Ring buffer of 360 samples (88 B each, 31,680 B RAM); oldest overwritten when full, `dropped_count` reported; flushed oldest-first on reconnect | Confirm. |
| 15 | LED/buzzer mapping | [INSERT FROM FIRMWARE] | One LED per state; buzzer off SAFE/WATCH, slow beep WARNING (300 ms / 1.5 s), fast beep CRITICAL (120 ms / 240 ms); fault = all LEDs flash 100 ms/s + chirp every 15 s; DEGRADED = state LED blinks off 150 ms every 2 s | Confirm and fill Table 6.1. |
| 16 | Hybrid decision policy | [VERIFY: select policy] | Rule-authoritative + disagreement flag on dashboard and event log | Confirm. |
| 17 | ML feature list, split, hyperparameters, label origin | [VERIFY]/[INSERT] | See `docs/DATASET_AND_ML.md` | **Label origin of the CSV is unknown** — state it. |
| 18 | Board model, resistor values, buzzer driver, supply | [INSERT]/[VERIFY] | Not assumed. README states the wiring you gave (220–330 Ω, transistor if needed) | Measure/inspect the real board and fill Tables 5.1/5.2. |
| 19 | Sensor polarity (soil output falls when wetter?) | [VERIFY: polarity] | Not assumed: the calibration wizard reports the measured polarity | Use the wizard's output. |
| 20 | GLC version and date range | [INSERT VERSION AND DATE RANGE] | Not available (dataset not supplied) | Download, run `historical/analyze_glc.py`, copy `summary.json` values. |

## B. Conflicts or mismatches

| # | Issue | Resolution in software |
|---|---|---|
| B1 | Report Table 8.1 lists **rain duration** as an ML feature; the synthetic CSV has **no rain-duration column**. | The model uses the 5 features that exist. The rule engine still uses d_r. Documented in `ml/outputs/metrics.json`. |
| B2 | CSV `moisture_index`/`rain_index` are **0–100**; the report and firmware use **[0, 1]**. CSV `moisture_rate_per_min` is in percentage points/min. | Training divides by 100 (indices and moisture rate). Telemetry, API, dashboard and simulator all use [0, 1] and per-minute rates. |
| B3 | Report Listing A.2 uses `Wire.begin()` (default pins 21/22); the **older firmware used SDA 21 / SCL 22**, but the current wiring is **SDA 25 / SCL 26**. | Firmware uses 25/26. If the old firmware is still flashed, the MPU6050 can't be found — a likely cause of the observed I²C fault. |
| B4 | The provisional rule engine agrees with the CSV's labels on only **80.0 %** of rows (it says SAFE for 799 of the 2662 WATCH rows). | Expected: the CSV labels were not produced with these provisional thresholds. This is **not** a validation of either. If the CSV labels came from a specific rule set, those thresholds should be adopted and documented. |
| B5 | Report's GPIO Table 5.1 template puts "[ADC1 / ADC2?]" next to SDA. | GPIO 25/26 are ADC2-capable pins, but here they are used only as digital I²C, which is fine with Wi-Fi. Soil/rain are on ADC1 (34/35). |

## C. Notes

* **C1 Tilt measured as inclination magnitude.** `α` (Eq. 6.4) changes only when the sensor's z-axis tilts relative
  to vertical. Rotating the model so that α stays the same (e.g. tilting the other direction at the same angle) is
  invisible to Δθ. The firmware therefore also reports `tilt_vec_change_deg` (angle between current and baseline
  gravity vectors) as a **diagnostic only**; it is not used in the risk score. If the report adopts it, update Eq. 6.4.
* **C2 Uncalibrated sensors are excluded, not estimated.** Until `cal` succeeds, soil/rain report `UNCALIBRATED`,
  send `null` indices and are excluded from R. With nothing usable, the state is `UNKNOWN` (never SAFE).
* **C3 Rail handling.** A rain module powered at 3.3 V can legitimately read near 4095 when dry. A reading at a
  rail is treated as a fault unless a valid saved calibration endpoint sits at that same rail.
