"""Simulated ESP32 for testing the software without hardware.

    python -m scripts.simulate_device --scenario progressive_failure
    python -m scripts.simulate_device --scenario sudden_slip --speed 4 --offline-at 60 --offline-for 20

Sends telemetry schema 2.0 to POST /api/readings as device "sim-node-1" with
origin="simulated" (shown as SIMULATED on the dashboard and never used as
experimental training data). The sensor traces come from the synthetic
scenario generator (ml/synthetic.py). It mimics the firmware: a calibration
phase, readings every 2 s, offline buffering with replay=true after a
simulated network outage, and newest-first delivery after reconnecting.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import secrets
import sys
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml.synthetic import SCENARIOS, simulate_session  # noqa: E402

DEVICE_ID = "sim-node-1"


def load_token(device_id: str) -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("LANDSLIDE_DEVICE_TOKENS="):
                for item in line.split("=", 1)[1].split(","):
                    if ":" in item and item.split(":", 1)[0].strip() == device_id:
                        return item.split(":", 1)[1].strip()
    raise SystemExit(f"no token for {device_id} in .env (run setup first)")


def _num(v, digits=4):
    if v is None:
        return None
    v = float(v)
    return round(v, digits) if math.isfinite(v) else None


class SimDevice:
    def __init__(self, scenario: str, seed: int, interval: float):
        self.rng = np.random.default_rng(seed)
        self.scenario = scenario
        self.interval = interval
        self.boot_id = secrets.token_hex(4)
        self.session_no = 1
        self.session_id = f"{self.boot_id}-{self.session_no:02d}"
        self.seq = 0
        _, self.tr = simulate_session(self.session_id, scenario, self.rng, return_traces=True)
        self.T = len(self.tr["tilt"])
        self.start_uptime = 3000
        self.incl0 = float(self.rng.uniform(5, 25))
        self.cal = {"status": "IN_PROGRESS", "trigger": "boot", "error": None, "attempts": 0,
                    "mount_axis": "+Z", "samples": 200}

    def calibrated(self, t_s: int) -> dict:
        tr = self.tr
        return {
            "status": "OK", "trigger": "boot", "error": None, "attempts": 1, "completed_uptime_ms": self.start_uptime + 5000,
            "gravity0": [round(math.sin(math.radians(self.incl0)), 5), 0.0, round(math.cos(math.radians(self.incl0)), 5)],
            "gyro_bias_dps": [round(float(x), 3) for x in self.rng.normal(0, 1.2, 3)],
            "incl0_deg": round(self.incl0, 3), "accel_std_g": 0.0021, "gyro_std_dps": 0.061,
            "vib_bg_eps": float(tr["vib_bg"]), "soil_raw0": int(tr["soil_raw"][0]), "water_raw0": int(tr["water_raw"][0]),
            "prev_incl0_deg": None, "prev_shift_deg": None, "nvs_saved": True, "mount_axis": "+Z", "samples": 200,
        }

    def reading(self, t_s: int, calibrating: bool, queued: int) -> dict:
        tr = self.tr
        i = min(max(t_s, 0), self.T - 1)
        self.seq += 1
        uptime = self.start_uptime + int((t_s + 6) * 1000)
        valid = not calibrating and t_s >= 0

        def f(key, digits=4):
            return _num(tr[key][i], digits) if valid else None

        tilt = float(tr["tilt"][i]) if valid else None
        ev = tr["events"]
        win = lambda n: int(ev[max(0, i - n + 1): i + 1].sum()) if valid and i + 1 >= n else None  # noqa: E731
        eps = float(ev[max(0, i - 1): i + 1].sum()) / 2.0 if valid else None
        bg = float(tr["vib_bg"]) if valid else None
        ev10, ev30 = win(10), win(30)
        return {
            "schema": "2.0", "feature_schema": "fs-1", "origin": "simulated", "device_id": DEVICE_ID, "fw": "sim-4.0.0",
            "boot_id": self.boot_id, "session_id": self.session_id,
            "reading_id": f"{self.session_id}-{self.seq:06d}", "seq": self.seq, "uptime_ms": uptime,
            "epoch_ms": int(time.time() * 1000), "replay": False,
            "op_state": "CALIBRATING" if calibrating else "MONITORING", "rssi": int(self.rng.integers(-72, -48)),
            "faults": [], "buffer": {"queued": queued, "dropped": 0},
            "cal": self.cal if calibrating else self.calibrated(t_s),
            "soil": {"raw": int(tr["soil_raw"][i]), "filt": _num(tr["soil_filt"][i], 1), "do": int(tr["soil_moisture"][i] < 0.6),
                     "moisture": _num(tr["soil_moisture"][i]), "pct": _num(tr["soil_moisture"][i] * 100, 2),
                     "rate_per_min": f("soil_rate", 5), "mean_30s": f("soil_mean"), "std_30s": f("soil_std", 5),
                     "health": "OK", "flags": ["ADC_DRY_SATURATION"] if tr["soil_raw"][i] >= 4080 else []},
            "water": {"raw": int(tr["water_raw"][i]), "filt": _num(tr["water_filt"][i], 1),
                      "wetness": _num(tr["water_wetness"][i]), "pct": _num(tr["water_wetness"][i] * 100, 2),
                      "wet_s": f("wet_s", 1), "rate_per_min": f("water_rate", 5), "mean_30s": f("water_mean"),
                      "health": "OK", "flags": ["AT_DRY_ENDPOINT"] if tr["water_raw"][i] <= 0 else []},
            "mpu": {"ax": _num(math.sin(math.radians(self.incl0 + (tilt or 0))), 4), "ay": 0.002,
                    "az": _num(math.cos(math.radians(self.incl0 + (tilt or 0))), 4), "gx": 0.01, "gy": 0.02, "gz": -0.01,
                    "incl0_deg": round(self.incl0, 3) if valid else None,
                    "incl_deg": _num(self.incl0 + (tilt or 0), 3) if valid else None,
                    "tilt_deg": _num(tilt, 3), "incl_change_deg": _num(tilt, 3), "roll_deg": 0.1,
                    "pitch_deg": _num(-(self.incl0 + (tilt or 0)), 2), "tilt_rate_dpm": f("tilt_rate", 3),
                    "acc_mag_g": 1.0012, "acc_dyn_g": f("accel_dyn", 5), "gyro_mag_dps": f("gyro_mag", 3),
                    "temp_c": 27.4, "health": "OK" if valid else "NOT_CALIBRATED", "flags": [] if valid else ["NO_BASELINE"]},
            "vib": {"do": int(ev[i] > 0), "transitions": int(ev[max(0, i - 1): i + 1].sum() * 3),
                    "events": int(ev[max(0, i - 1): i + 1].sum()), "window_ms": 2000, "eps": eps, "bg_eps": bg,
                    "above_bg_eps": max(0.0, eps - bg) if valid else None, "ev_5s": win(5), "ev_10s": ev10, "ev_30s": ev30,
                    "trend_eps": (ev10 / 10 - (ev30 - ev10) / 20) if ev10 is not None and ev30 is not None else None,
                    "health": "OK"},
        }


def post(url: str, token: str, payload: dict, timeout: float = 4.0) -> tuple[int, dict | None]:
    data = json.dumps(payload, allow_nan=False).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "X-Device-Id": DEVICE_ID, "X-Device-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"null")
        except ValueError:
            return exc.code, None
    except (urllib.error.URLError, OSError):
        return -1, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Simulated ESP32 (origin=simulated)")
    ap.add_argument("--scenario", choices=list(SCENARIOS), default="progressive_failure")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--speed", type=float, default=1.0, help="time acceleration (2 = twice as fast)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--offline-at", type=float, default=None, help="simulated seconds into monitoring")
    ap.add_argument("--offline-for", type=float, default=20.0)
    ap.add_argument("--loop", action="store_true", help="repeat with new sessions until interrupted")
    args = ap.parse_args(argv)
    url = f"http://{args.host}:{args.port}/api/readings"
    token = load_token(DEVICE_ID)
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    while True:
        dev = SimDevice(args.scenario, seed, 2.0)
        print(f"SIMULATED device {DEVICE_ID}: scenario={args.scenario} session={dev.session_id} "
              f"({dev.T} s of monitoring) -> {url}")
        queue: deque = deque(maxlen=120)
        t_cal = -6
        t = t_cal
        last_state = None
        while t < dev.T:
            calibrating = t < 0
            r = dev.reading(t, calibrating, len(queue))
            offline = (args.offline_at is not None and not calibrating
                       and args.offline_at <= t < args.offline_at + args.offline_for)
            if offline:
                queue.append(r)
                if last_state != "offline":
                    print(f"  t={t:4d}s network outage simulated; buffering")
                last_state = "offline"
            else:
                code, resp = post(url, token, r)
                if code in (200, 201) and resp:
                    shown = resp.get("risk_state") or resp.get("prediction_status")
                    if shown != last_state:
                        print(f"  t={t:4d}s label={dev.tr['label'][max(0, min(t, dev.T - 1))]:8s} "
                              f"backend={shown} {resp.get('reason') or ''}")
                        last_state = shown
                    while queue:  # drain backlog after the live reading, oldest first
                        old = queue.popleft()
                        old["replay"] = True
                        post(url, token, old)
                else:
                    print(f"  t={t:4d}s HTTP {code} {resp if resp else ''}")
                    queue.append(r)
            t += 2
            time.sleep(2.0 / max(args.speed, 0.01))
        if not args.loop:
            return 0
        seed += 1


if __name__ == "__main__":
    sys.exit(main())
