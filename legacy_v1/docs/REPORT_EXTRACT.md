# What the report (`main.pdf`) specifies — extraction used for implementation

Source: `docs/reference/main.pdf` (unchanged copy; SHA-256 `1dd916d45fd34125fe0f9e3fe009bef2f98c18057840053f84ce89d615090e0a`).
Text was extracted with `pdftotext -layout` and read chapter by chapter (Ch. 3–10, 12, Appendices A–B).
"Report value" = stated in the report. "[INSERT]/[VERIFY]/[DATA]" = left open by the report.

## Equations (Chapter 6) and how the software implements them

| Report | Equation | Implementation |
|---|---|---|
| Eq. 6.1 | `m[n] = min(1, max(0, (m_dry − m̃_raw[n]) / (m_dry − m_wet)))` | `normalizeIndex()` in the .ino, `normalize_index()` in Python. Also used for rain (Sec. 6.3 "normalized in the same manner"). Works for either sensor polarity. |
| Eq. 6.2 | `d_r[n] = d_r[n−1] + Δt` if `r[n] ≥ r_th`, else `0` | Implemented as written (reset to 0 when dry). Report marks reset-vs-decay as [VERIFY]. |
| Eq. 6.3 | `a_i = a_i,raw / S`, S = 16384 LSB/g at ±2 g | `ACC_SENS_LSB_PER_G = 16384`, MPU6050 configured to ±2 g. |
| Eq. 6.4 | `α = atan2(√(a_x² + a_y²), a_z)`, `Δθ = α − α₀` | Implemented on the complementary-filtered gravity vector (report allows "a complementary or similar filter"; formulation is [VERIFY]). |
| Eq. 6.5 | N-point causal moving average | `maAdd()`; N is [INSERT] → provisional N = 10. |
| Eq. 6.6 | Baseline `x₀ = (1/N_c) Σ x[i]` | MPU6050 baseline over N_c = 200 samples (provisional) in the `cal`/`baseline` wizard. Report: conditions and N_c [INSERT]. |
| Eq. 6.7 | `ẋ[n] ≈ (x̃[n] − x̃[n−L]) / (L·Δt)` | `rhRatePerMin()`, expressed **per minute** (× 60). L is [INSERT] → provisional L = 30. |
| Eq. 6.8 | `C(x; x_lo, x_hi) = min(1, max(0, (x − x_lo)/(x_hi − x_lo)))` | `riskComponent()`. Thresholds [INSERT FROM FIRMWARE] → provisional values in the .ino CONFIG block. |
| Eq. 6.9 | `M = (1 − β_M)·C(m) + β_M·C(ṁ)`, analogously `R_r` (r, d_r) and `T` (Δθ, θ̇) | `riskMix()`. β is [INSERT] → provisional β = 0.3 for all three. |
| Eq. 6.10 | `R = 0.40 M + 0.30 R_r + 0.30 T` | **Report value**, used unchanged. When a component is faulty/uncalibrated, weights are re-normalized over the usable ones (identical to Eq. 6.10 when all three are usable). |
| Eq. 6.11 | SAFE `R < τ1`; WATCH `τ1 ≤ R < τ2`; WARNING `τ2 ≤ R < τ3`; CRITICAL `R ≥ τ3` | `riskClassify()`. τ1–τ3 are [INSERT] → provisional 0.25/0.50/0.75. |

Algorithm 1 (report p. 27) is the loop in `analogAndRiskStep()`; network transmission is done by a separate FreeRTOS task so it never blocks the loop (report Sec. 9.10 requires this).

## Values the report actually states
* Weights 0.40 / 0.30 / 0.30 (Eq. 6.10, "prototype design parameters", not geotechnical constants).
* MPU6050 sensitivity 16384 LSB/g at ±2 g; default I²C address 0x68 (AD0 low).
* 12-bit ADC (0–4095); analog sensors should use ADC1 while Wi-Fi is active.
* Four states SAFE/WATCH/WARNING/CRITICAL (prototype states, not official warnings).
* NFR-2 design target: < 1 s from sampling to local indication (measured value [INSERT]).
* Representative telemetry fields (Table 9.1) and endpoints `/api/readings`, `/api/latest`, `/api/history`, `/api/events` (Table 9.2) — implemented with these names.
* GLC ≈ 11,033 records; reported trigger shares rain 38 %, monsoon 18 %, earthquake 10 %, construction 6 % — marked [RE-VERIFY] in the report; `historical/analyze_glc.py` recomputes them.
* Rule engine is authoritative; ML is advisory; hybrid policy [VERIFY] → implemented "rule-authoritative + agreement flag" (Table 8.4 rows 1 and 3).
* Split by experimental run for ML (Sec. 8.6, Listing A.6 uses GroupShuffleSplit).

## Stated limitations (carried into README and UI wording)
* Not scientifically validated for real-world prediction, timing or probability (Ch. 1, 12, 14).
* Soil index ≠ volumetric water content; rain activity ≠ rainfall rate; tilt ≠ landslide displacement (Table 5.3).
* Uncalibrated low-cost sensors; MPU6050 drift; miniature model not a scaled replica; small dataset; possible label dependency (rule-derived labels ⇒ ML only reproduces rules, Sec. 8.16); historical catalogue bias; no field validation (Table 12.1).
* GLC must not train the sensor model (Sec. 7.8.1).

## Placeholders in the report
See `docs/PLACEHOLDER_MAP.md` for every [INSERT]/[DATA]/[VERIFY] and the command or experiment that fills it.
