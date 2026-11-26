# Automatic calibration

No manual calibration commands exist. Calibration runs automatically at **power-on, every restart, and
whenever `demo` is typed** in the Serial Monitor. Keep the slope model completely still for about 5 s.

## Fixed endpoints (never changed at runtime)
| Sensor | Dry | Wet | Index |
|---|---:|---:|---|
| Soil moisture (AO, GPIO 35) | 4090 | 921 | `clamp((4090 − raw) / (4090 − 921), 0, 1)` |
| Water sensor (S, GPIO 34) | 0 | 1725 | `clamp(raw / 1725, 0, 1)` |

These come from the initial hardware tests. Startup calibration does **not** redefine the current soil or
water condition as dry/wet; it only records the current raw values (`soil_raw0`, `water_raw0`) for diagnostics.

## Sequence (non-blocking state machine; telemetry continues with `op_state: CALIBRATING`)
1. GPIO and ADC (12-bit, 11 dB) initialisation; MPU6050 detection at 0x68/0x69 (±2 g, ±250 °/s, DLPF ≈ 21 Hz, 50 Hz).
2. New session ID (`<boot_id>-<n>`), all trend histories reset, previous session's prediction discarded.
3. 0.5 s settle, then 200 valid MPU6050 samples at 50 Hz (~4 s) while SW-420 events are counted → background rate.
4. Stability checks: accelerometer std ≤ 0.03 g, gyro std ≤ 1.5 °/s, |g| within 0.90–1.10 g, gyro bias ≤ 20 °/s
   per axis, no sample above 30 °/s, ≤ 20 samples outside 0.8–1.2 g, collection within 8 s.
5. Gravity baseline (unit vector), gyro bias and initial inclination locked for the session.
6. NVS: the previous record is copied to `calprev`, the new one written to `cal` (CRC-protected; previous first,
   so a power cut leaves a valid record). NVS is written once per successful calibration.
7. Monitoring and telemetry continue; predictions start when the 30-s windows are full.

## Failure handling
* The exact reason is printed and sent (`cal.error`: `MPU_NOT_DETECTED`, `MPU_READ_FAILED`,
  `MOVEMENT_OR_INVALID_ACCEL`, `UNSTABLE_MOVEMENT`, `INVALID_GRAVITY_MAGNITUDE`, `GYRO_BIAS_OUT_OF_RANGE`,
  `SAMPLE_TIMEOUT`).
* Up to 3 attempts, 2 s apart. After that the session is `CALIBRATION_FAILED`: tilt features stay `null`,
  the backend answers `SENSOR_FAULT` (never SAFE) and the LEDs show the sensor-fault pattern.
* The previously saved baseline is kept in NVS but **not** used for the new session.
* If calibration failed because the MPU6050 was missing and it is detected later, calibration is retried
  automatically (at most twice) as a new session. Other failures wait for `demo`.

## Baseline lock and its limitation
The baseline stays locked until the next `demo` or restart. Because every restart recalibrates, movement
that happened while the device was off becomes the new zero. To keep that visible, the angle between the
new baseline and the previously saved one is reported as `cal.prev_shift_deg` (dashboard: Device & system →
Calibration, and the `CALIBRATION_OK` event).

## Soil ADC saturation
| Condition | Treatment |
|---|---|
| raw ≥ 4080 | valid dry end: moisture 0, flag `ADC_DRY_SATURATION`, health OK |
| saturated, noiseless for ≥ 10 min | additional flag `ADC_PERSISTENT_SATURATION` (still classified; check wiring if soil should not be dry) |
| saturated while the DO comparator indicates wet | health `SUSPECT`, flag `DO_DISAGREES` (classified, data quality DEGRADED) |
| raw < 300 | `FAULT` `ADC_LOW_RAIL` (short/disconnected) — no prediction |
| std of the 1-s window > 300 counts | `FAULT` `ADC_UNSTABLE` (floating input) — no prediction |

Raw ADC values are always transmitted. DO polarity is configurable (`SOIL_DO_WET_LEVEL`, default LOW = wet).

## Water sensor
Raw 0 is the normal dry value (flag `AT_DRY_ENDPOINT`); a disconnected sensor can also read 0, so its
connectivity cannot be confirmed in that state. ≥ 4080 → `FAULT` (`ADC_HIGH_RAIL`), > 2300 → `SUSPECT`,
noise > 300 counts → `FAULT`.

## SW-420
Interrupt on every edge; a filtered event requires 20 ms since the previous one. Background rate is measured
during calibration. Counts are pulse events, not amplitude, magnitude or frequency, and a digital output
cannot prove that the sensor is connected.
