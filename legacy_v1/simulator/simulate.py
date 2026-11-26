"""Send SIMULATED readings to the backend (POST /api/readings, source="simulated").

Replay the synthetic CSV:
    python simulator/simulate.py replay --experiments EXP-001,EXP-002 --speed 60
Generate a live scenario:
    python simulator/simulate.py live --scenario heavy_rain --duration 180
    python simulator/simulate.py live --scenario combined_stress --duration 120 --fast
Demonstrate fault handling (MPU6050 excluded, weights re-normalized):
    python simulator/simulate.py live --scenario gradual_wetting --fault mpu

Every record is tagged source="simulated" and the dashboard shows a SIMULATED
banner. Rule states are computed with the Python port of the firmware risk
engine (same parameters, read from the .ino). Nothing here is physical data.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from collections import deque
from pathlib import Path

import pandas as pd
import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from backend.ino_config import load_ino_config  # noqa: E402
from backend.risk_engine import (STATE_NAMES, RainDuration, RiskInputs,  # noqa: E402
                                 RiskMachine, RiskParams, compute)

DEFAULT_CSV = REPO / "ml" / "data" / "landslide_synthetic_experimental_dataset_5000.csv"
SCENARIOS = ("dry", "gradual_wetting", "heavy_rain", "combined_stress")


class RuleTracker:
    """Applies the firmware rule engine + state machine to a simulated stream."""

    def __init__(self):
        self.cfg = load_ino_config()
        self.p = RiskParams.from_ino()
        self.machine = RiskMachine(self.p)
        self.rain = RainDuration(self.cfg["RAIN_WET_THRESHOLD"])

    def step(self, *, m, r, tilt, dm, tr, dt_s, now_ms, rates_ready=True, m_ok=True, r_ok=True, t_ok=True) -> dict:
        d = self.rain.update(r if r_ok else None, dt_s)
        out = compute(RiskInputs(m=m or 0.0, dm=dm or 0.0, r=r or 0.0, drS=d, tilt=tilt or 0.0, tiltRate=tr or 0.0,
                                 mOk=m_ok, rOk=r_ok, tOk=t_ok, ratesReady=rates_ready), self.p)
        state = self.machine.step(out, now_ms)
        usable = out.usable
        health = "OK" if usable == 3 else ("DEGRADED" if usable else "FAULT")
        nn = lambda v: None if v < 0 else round(v, 4)  # noqa: E731
        return {"rain_duration_s": round(d, 1) if r_ok else 0.0, "comp_m": nn(out.compM), "comp_r": nn(out.compR),
                "comp_t": nn(out.compT), "risk_score": nn(out.score), "risk_state": STATE_NAMES[state],
                "raw_state": STATE_NAMES[out.raw], "health": health, "rates_ready": rates_ready}


def post(url: str, batch: list[dict]):
    r = requests.post(url, json=batch, timeout=10)
    r.raise_for_status()
    return r.json()


def replay(args):
    df = pd.read_csv(args.csv)
    if args.experiments:
        keep = [e.strip() for e in args.experiments.split(",")]
        df = df[df["experiment_id"].isin(keep)]
    if df.empty:
        raise SystemExit("No rows selected.")
    url = args.url.rstrip("/") + "/api/readings"
    seq = 0
    for exp_id, grp in df.groupby("experiment_id", sort=False):
        tracker = RuleTracker()
        ts = pd.to_datetime(grp["timestamp"])
        t0 = ts.iloc[0]
        batch = []
        print(f"[SIMULATED replay] {exp_id}: {len(grp)} rows")
        prev_t = None
        for (_, row), t in zip(grp.iterrows(), ts):
            dt_s = 60.0 if prev_t is None else (t - prev_t).total_seconds()
            prev_t = t
            m, r = row["moisture_index"] / 100.0, row["rain_index"] / 100.0           # CSV 0-100 -> [0,1]
            dm = row["moisture_rate_per_min"] / 100.0                                    # pct-points/min -> index/min
            tilt, tr = abs(row["tilt_change_deg"]), row["tilt_rate_deg_per_min"]
            now_ms = int((t - t0).total_seconds() * 1000)
            rule = tracker.step(m=m, r=r, tilt=tilt, dm=dm, tr=tr, dt_s=dt_s, now_ms=now_ms)
            seq += 1
            rec = {"device_id": f"sim-replay-{exp_id}", "seq": seq, "source": "simulated", "fw_version": "simulator",
                   "moisture": round(m, 4), "rain": round(r, 4), "tilt_deg": round(tilt, 3),
                   "moisture_rate_per_min": round(dm, 5), "tilt_rate_deg_per_min": round(tr, 5),
                   "soil_status": "OK", "rain_status": "OK", "mpu_status": "OK", "faults": [],
                   "scenario": args.scenario or f"replay:{exp_id}", "dataset_label": row["risk_label"], **rule}
            batch.append(rec)
            if args.speed > 0:
                post(url, batch)
                batch = []
                time.sleep(dt_s / args.speed)
            elif len(batch) >= 100:
                post(url, batch)
                batch = []
        if batch:
            post(url, batch)
    print(f"[SIMULATED replay] sent {seq} readings to {url}")


def scenario_value(name: str, frac: float, rng: random.Random) -> tuple[float, float, float]:
    """Return (moisture index, rain activity, tilt change deg) at fraction 0..1 of the run."""
    n = lambda s: rng.gauss(0, s)  # noqa: E731
    clip = lambda v: min(1.0, max(0.0, v))  # noqa: E731
    if name == "dry":
        return clip(0.15 + n(0.01)), clip(0.02 + abs(n(0.01))), 0.05 + n(0.03)
    if name == "gradual_wetting":
        return clip(0.15 + 0.7 * frac + n(0.01)), clip((0.25 if (frac * 20) % 4 < 1 else 0.02) + n(0.02)), 0.1 + 0.3 * frac + n(0.03)
    if name == "heavy_rain":
        on = frac > 0.1
        m = 0.2 + (0.75 * min(1.0, (frac - 0.1) / 0.4) if on else 0.0)
        return clip(m + n(0.01)), clip((0.92 if on else 0.02) + n(0.03)), 0.1 + 0.8 * max(0.0, frac - 0.5) + n(0.03)
    if name == "combined_stress":
        m = 0.3 + 0.62 * min(1.0, frac / 0.5)
        tilt = 0.1 + (5.0 * (frac - 0.5) / 0.5 if frac > 0.5 else 0.0)
        return clip(m + n(0.01)), clip(0.85 + n(0.03)), tilt + n(0.03)
    raise ValueError(name)


def generate_live(scenario: str, duration_s: int, interval_s: float = 1.0, seed: int = 1, fault: str | None = None):
    """Yield simulated records (dicts) for a scenario. Rates use the firmware window L (per minute)."""
    cfg = load_ino_config()
    L = cfg["RATE_WINDOW_L"]
    rng = random.Random(seed)
    tracker = RuleTracker()
    hm, ht = deque(maxlen=L + 1), deque(maxlen=L + 1)
    steps = max(1, int(duration_s / interval_s))
    for k in range(steps):
        frac = k / max(1, steps - 1)
        m, r, tilt = scenario_value(scenario, frac, rng)
        hm.append(m)
        ht.append(abs(tilt))
        ready = len(hm) == L + 1
        dm = (hm[-1] - hm[0]) / (L * interval_s) * 60 if ready else 0.0
        tr = (ht[-1] - ht[0]) / (L * interval_s) * 60 if ready else 0.0
        t_ok = fault != "mpu"
        r_ok = fault != "rain"
        rule = tracker.step(m=m, r=r, tilt=tilt, dm=dm, tr=tr, dt_s=interval_s, now_ms=int(k * interval_s * 1000),
                            rates_ready=ready, r_ok=r_ok, t_ok=t_ok)
        faults = (["MPU_I2C_ERROR"] if not t_ok else []) + (["RAIN_RAIL_LOW"] if not r_ok else [])
        yield {"device_id": f"sim-live-{scenario}", "seq": k + 1, "source": "simulated", "fw_version": "simulator",
               "device_uptime_ms": int(k * interval_s * 1000),
               "moisture": round(m, 4), "rain": round(r, 4) if r_ok else None,
               "tilt_deg": round(tilt, 3) if t_ok else None,
               "moisture_rate_per_min": round(dm, 5) if ready else None,
               "tilt_rate_deg_per_min": round(tr, 5) if (ready and t_ok) else None,
               "soil_status": "OK", "rain_status": "OK" if r_ok else "FAULT", "mpu_status": "OK" if t_ok else "FAULT",
               "faults": faults, "scenario": scenario, **rule}


def live(args):
    url = args.url.rstrip("/") + "/api/readings"
    n = 0
    last = None
    for rec in generate_live(args.scenario, args.duration, args.interval, args.seed, args.fault):
        if not args.fast:
            rec["device_time_ms"] = time.time() * 1000.0
        res = post(url, [rec])
        n += 1
        if res["rule_state"] != last:
            print(f"[SIMULATED {args.scenario}] t={rec['device_uptime_ms']/1000:6.0f}s R={rec['risk_score']} "
                  f"rule={res['rule_state']} ml={res['ml_state']} disagree={res['disagree']}")
            last = res["rule_state"]
        if not args.fast:
            time.sleep(args.interval)
    print(f"[SIMULATED {args.scenario}] sent {n} readings to {url}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="backend base URL (127.0.0.1 avoids a slow IPv6 localhost lookup on Windows)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("replay", help="replay the synthetic CSV")
    a.add_argument("--csv", default=str(DEFAULT_CSV))
    a.add_argument("--experiments", default="EXP-001", help="comma list of experiment_id values ('' = all)")
    a.add_argument("--speed", type=float, default=0, help="time multiplier (60 = 1 CSV minute per second); 0 = as fast as possible")
    a.add_argument("--scenario", default=None)
    b = sub.add_parser("live", help="generate a scenario in real time")
    b.add_argument("--scenario", choices=SCENARIOS, default="gradual_wetting")
    b.add_argument("--duration", type=int, default=120, help="seconds")
    b.add_argument("--interval", type=float, default=1.0)
    b.add_argument("--seed", type=int, default=1)
    b.add_argument("--fault", choices=["mpu", "rain"], default=None, help="simulate a sensor fault")
    b.add_argument("--fast", action="store_true", help="send without waiting (no device_time, no latency)")
    args = ap.parse_args(argv)
    try:
        replay(args) if args.cmd == "replay" else live(args)
    except requests.ConnectionError:
        raise SystemExit(f"Cannot reach {args.url}. Start the backend first: python -m backend.run")


if __name__ == "__main__":
    main()
