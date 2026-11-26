"""Synthetic four-sensor DEMONSTRATION dataset generator.

    python -m ml.synthetic                  -> datasets/synthetic/four_sensor_demo_v1.csv

WHAT THIS IS
    Simulated miniature-slope sessions used ONLY to demonstrate and test the
    software pipeline (telemetry -> features -> Random Forest -> alerts) when
    no independently labelled real four-sensor data exists.

WHAT IT IS NOT
    Not measurements, not real landslide outcomes, not evidence of prediction
    accuracy. A model trained on it learns the generator's scenario script.

HOW LABELS ARE DEFINED
    Each session follows a scripted scenario (stable, disturbance, wetting,
    saturation creep, progressive failure, sudden slip, sustained vibration).
    The label of a reading is the scripted latent stage at that time:
      SAFE     stable conditions (background vibration, no movement)
      WATCH    one precursor elevated: wetting / water exposure, or sustained
               vibration, without slope movement
      WARNING  saturated soil with slow creep (tilt rate ~0.3-3 deg/min), or a
               displaced but currently stationary slope after a slip
      CRITICAL active movement (tilt rate > ~5 deg/min) with vibration bursts
    Stage changes are jittered relative to the physical signals and sensor
    noise is added, so labels are not a deterministic function of features.
    The old weighted 40/30/30 risk formula is NOT used anywhere.

Features are computed exactly as the firmware does (1 Hz snapshots, readings
every 2 s, 30 s regression slopes, 10 s tilt slope, 30-bin vibration window).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .features import FEATURE_NAMES

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DEFAULT = REPO_ROOT / "datasets" / "synthetic" / "four_sensor_demo_v1.csv"
STAGES = ["SAFE", "WATCH", "WARNING", "CRITICAL"]
SCENARIOS = {  # name: probability
    "stable": 0.18,
    "disturbance": 0.12,
    "wetting": 0.18,
    "saturation_creep": 0.16,
    "progressive_failure": 0.18,
    "sudden_slip": 0.09,
    "sustained_vibration": 0.09,
}
SOIL_DRY, SOIL_WET, WATER_WET = 4090, 921, 1725


def _rolling_slope_per_min(values: np.ndarray, n: int) -> np.ndarray:
    """Least-squares slope (per minute) over the last n 1-s samples; NaN until full."""
    out = np.full(len(values), np.nan)
    if len(values) < n:
        return out
    t = np.arange(n) - (n - 1) / 2.0
    kernel = t / np.sum(t * t)
    out[n - 1:] = np.convolve(values, kernel[::-1], mode="valid") * 60.0
    return out


def _rolling(values: np.ndarray, n: int, func) -> np.ndarray:
    s = pd.Series(values).rolling(n, min_periods=n)
    return getattr(s, func)(ddof=1).to_numpy() if func == "std" else getattr(s, func)().to_numpy()


def _schedule(scenario: str, rng: np.random.Generator) -> tuple[list[tuple[str, int]], dict]:
    """Stage schedule [(stage, seconds)] and physical drivers for one session."""
    d = lambda a, b: int(rng.integers(a, b))  # noqa: E731
    p: dict = {"rain": [], "creep": [], "rapid": [], "vib": [], "slip_step": None}
    if scenario == "stable":
        sched = [("SAFE", d(300, 600))]
    elif scenario == "disturbance":
        sched = [("SAFE", d(300, 600))]
        for _ in range(int(rng.integers(2, 6))):  # footsteps / tapping: short bursts, no movement
            s = d(40, sched[0][1] - 40)
            p["vib"].append((s, s + d(5, 30), rng.uniform(0.8, 4.0)))
    elif scenario == "wetting":
        a, b = d(60, 150), d(200, 400)
        sched = [("SAFE", a), ("WATCH", b)]
        p["rain"].append((a - d(0, 30), a + d(60, 200), rng.uniform(0.35, 0.95)))
    elif scenario == "saturation_creep":
        a, b, c = d(60, 120), d(120, 240), d(120, 300)
        sched = [("SAFE", a), ("WATCH", b), ("WARNING", c)]
        p["rain"].append((a - d(0, 20), a + b + d(0, c), rng.uniform(0.5, 1.0)))
        p["creep"].append((a + b + d(-10, 10), a + b + c, rng.uniform(0.3, 3.0)))
        p["vib"].append((a + b, a + b + c, rng.uniform(0.0, 0.6)))
    elif scenario == "progressive_failure":
        a, b, c, e = d(60, 120), d(90, 200), d(90, 200), d(40, 150)
        sched = [("SAFE", a), ("WATCH", b), ("WARNING", c), ("CRITICAL", e)]
        p["rain"].append((a - d(0, 20), a + b + c + e, rng.uniform(0.5, 1.0)))
        p["creep"].append((a + b + d(-10, 10), a + b + c, rng.uniform(0.3, 3.0)))
        p["rapid"].append((a + b + c + d(-5, 5), a + b + c + e, rng.uniform(5.0, 45.0)))
        p["vib"].append((a + b + c, a + b + c + e, rng.uniform(1.5, 8.0)))
    elif scenario == "sudden_slip":
        a, e, w = d(90, 240), d(20, 60), d(120, 300)
        sched = [("SAFE", a), ("CRITICAL", e), ("WARNING", w)]
        p["rapid"].append((a, a + min(e, d(8, 25)), rng.uniform(15.0, 60.0)))
        p["vib"].append((a, a + e, rng.uniform(2.0, 10.0)))
    else:  # sustained_vibration (machinery/traffic): WATCH without movement
        a, b = d(60, 180), d(120, 300)
        sched = [("SAFE", a), ("WATCH", b)]
        p["vib"].append((a, a + b, rng.uniform(1.5, 8.0)))
    return sched, p


def simulate_session(session_id: str, scenario: str, rng: np.random.Generator, return_traces: bool = False):
    """One simulated session. With return_traces=True also returns the per-second raw traces
    (used by scripts/simulate_device.py to build full telemetry payloads)."""
    sched, drv = _schedule(scenario, rng)
    T = sum(s for _, s in sched)
    label = np.empty(T, dtype=object)
    pos = 0
    for stage, secs in sched:
        label[pos:pos + secs] = stage
        pos += secs
    t = np.arange(T, dtype=float)

    # --- water exposure (relative index; 0 dry .. 1 wet) -------------------
    w_true = np.zeros(T)
    for s, e, intensity in drv["rain"]:
        s, e = max(0, s), min(T, e)
        w_true[s:e] = intensity * (1 + 0.15 * rng.standard_normal(e - s)).clip(0.3, 1.5)
    # plate dries with a ~20-60 s time constant after exposure stops
    tau = rng.uniform(20, 60)
    for i in range(1, T):
        if w_true[i] == 0 and w_true[i - 1] > 0:
            w_true[i] = w_true[i - 1] * np.exp(-1 / tau)
    w_raw = np.clip(np.round(w_true * WATER_WET + rng.normal(0, 8, T)), 0, 4095)

    # --- soil moisture (infiltration / drainage) ---------------------------
    m = np.empty(T)
    m[0] = rng.uniform(0.02, 0.45) if scenario not in ("saturation_creep", "progressive_failure") else rng.uniform(0.15, 0.45)
    k_in, k_out = rng.uniform(0.004, 0.012), rng.uniform(0.0002, 0.0008)
    m_eq = m[0]
    for i in range(1, T):
        m[i] = m[i - 1] + k_in * w_true[i] * (1 - m[i - 1]) - k_out * (m[i - 1] - m_eq) + rng.normal(0, 0.0008)
    m = m.clip(0, 1)
    soil_raw = np.clip(np.round(SOIL_DRY - m * (SOIL_DRY - SOIL_WET) + rng.normal(0, 6, T)), 0, 4095)
    soil_filt = pd.Series(soil_raw).ewm(alpha=0.3).mean().to_numpy()
    water_filt = pd.Series(w_raw).ewm(alpha=0.3).mean().to_numpy()
    soil_m = ((SOIL_DRY - soil_filt) / (SOIL_DRY - SOIL_WET)).clip(0, 1)
    water_w = (water_filt / WATER_WET).clip(0, 1)

    # --- tilt (angle magnitude vs. baseline, degrees) ----------------------
    rate = np.zeros(T)  # deg/s
    rate += rng.normal(0, 0.0004, T)  # thermal / filter drift
    for s, e, r in drv["creep"]:
        s, e = max(0, s), min(T, e)
        rate[s:e] += (r / 60.0) * (1 + 0.3 * rng.standard_normal(e - s)).clip(0.1, 2)
    for s, e, r in drv["rapid"]:
        s, e = max(0, s), min(T, e)
        n = e - s
        envelope = np.exp(-np.linspace(0, rng.uniform(0.5, 2.5), n))  # decelerating movement
        rate[s:e] += (r / 60.0) * envelope * (1 + 0.25 * rng.standard_normal(n)).clip(0.2, 2)
    if scenario == "wetting":  # small settlement while wetting (no failure)
        rate += (rng.uniform(0, 0.08) / 60.0) * (w_true > 0)
    theta = np.cumsum(rate)
    noise = rng.normal(0, rng.uniform(0.01, 0.04), (T, 2))
    tilt = np.sqrt((theta + noise[:, 0]) ** 2 + noise[:, 1] ** 2).clip(0, 180)

    # --- SW-420 events per second -----------------------------------------
    lam0 = rng.choice([0.0, rng.uniform(0.0, 0.3)], p=[0.4, 0.6])
    lam = np.full(T, lam0)
    for s, e, lv in drv["vib"]:
        s, e = max(0, s), min(T, e)
        lam[s:e] += lv * (1 + 0.5 * rng.standard_normal(e - s)).clip(0.1, 3)
    lam += np.abs(rate) * 60 * 0.08  # movement makes the switch chatter
    events = np.minimum(rng.poisson(lam), 50)  # 20 ms dead time -> <= 50 filtered events/s
    bg = rng.poisson(lam0 * 4.0) / 4.0  # measured during the 4 s calibration window

    # --- accelerometer / gyroscope interval statistics -----------------------
    dyn_base = rng.uniform(0.0015, 0.004)
    accel_dyn = dyn_base + 0.004 * np.sqrt(lam) + 0.002 * np.abs(rate) + np.abs(rng.normal(0, 0.0007, T))
    gyro_base = rng.uniform(0.04, 0.15)
    gyro_mag = np.sqrt(gyro_base ** 2 + (np.abs(rate) * 1.2) ** 2 + (0.05 * lam) ** 2) * (1 + 0.1 * rng.standard_normal(T)).clip(0.5, 2)

    # --- firmware feature computation ---------------------------------------
    soil_rate = _rolling_slope_per_min(soil_m, 31)
    soil_std = _rolling(soil_m, 31, "std")
    water_mean = _rolling(water_w, 31, "mean")
    tilt_rate = _rolling_slope_per_min(tilt, 11)
    wet_dur = np.zeros(T)
    for i in range(T):
        wet_dur[i] = (wet_dur[i - 1] + 1 if i else 0) if water_w[i] >= 0.10 else 0
    ev = pd.Series(events)
    ev30 = ev.rolling(30, min_periods=30).sum().to_numpy()
    eps2 = ev.rolling(2, min_periods=2).sum().to_numpy() / 2.0

    idx = np.arange(31, T, 2)  # one reading every 2 s after the windows fill
    df = pd.DataFrame({
        "session_id": session_id,
        "scenario": scenario,
        "t_s": t[idx].astype(int),
        "label": label[idx],
        "soil_moisture": soil_m[idx],
        "soil_rate_per_min": soil_rate[idx],
        "soil_std_30s": soil_std[idx],
        "water_wetness": water_w[idx],
        "water_wet_duration_s": wet_dur[idx],
        "water_mean_30s": water_mean[idx],
        "tilt_deg": tilt[idx],
        "tilt_rate_deg_per_min": tilt_rate[idx],
        "accel_dyn_g": accel_dyn[idx],
        "gyro_mag_dps": gyro_mag[idx],
        "vib_events_per_s": eps2[idx],
        "vib_above_bg_per_s": np.maximum(0.0, eps2[idx] - bg),
        "vib_events_30s": ev30[idx],
    })
    # Label jitter: stage boundaries in real experiments are only known approximately.
    shift = int(rng.integers(-3, 4))
    if shift:
        df["label"] = df["label"].shift(shift).bfill().ffill()
    if not return_traces:
        return df
    traces = {
        "label": label, "soil_raw": soil_raw, "soil_filt": soil_filt, "soil_moisture": soil_m, "soil_rate": soil_rate,
        "soil_mean": _rolling(soil_m, 31, "mean"), "soil_std": soil_std, "water_raw": w_raw, "water_filt": water_filt,
        "water_wetness": water_w, "water_rate": _rolling_slope_per_min(water_w, 31), "water_mean": water_mean,
        "wet_s": wet_dur, "tilt": tilt, "tilt_rate": tilt_rate, "accel_dyn": accel_dyn, "gyro_mag": gyro_mag,
        "events": events, "vib_bg": bg,
    }
    return df, traces


def generate(n_sessions: int = 180, seed: int = 20261126) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    names, probs = zip(*SCENARIOS.items())
    frames = []
    for i in range(n_sessions):
        scenario = str(rng.choice(names, p=np.array(probs) / sum(probs)))
        frames.append(simulate_session(f"syn-{i + 1:04d}", scenario, rng))
    df = pd.concat(frames, ignore_index=True)
    df[FEATURE_NAMES] = df[FEATURE_NAMES].round(6)
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the SYNTHETIC four-sensor demonstration dataset")
    parser.add_argument("--sessions", type=int, default=180)
    parser.add_argument("--seed", type=int, default=20261126)
    parser.add_argument("--out", default=str(OUT_DEFAULT))
    args = parser.parse_args(argv)
    df = generate(args.sessions, args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"SYNTHETIC demonstration dataset: {len(df)} readings, {df['session_id'].nunique()} sessions -> {out}")
    print(df["label"].value_counts().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
