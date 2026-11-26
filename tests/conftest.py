"""Shared fixtures. Everything runs against temporary databases and artifact
folders; the real database, models and .env are never touched."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.config import Settings  # noqa: E402
from ml.features import FEATURE_NAMES  # noqa: E402

DEVICE_TOKEN = "test-device-token-0123456789"
SIM_TOKEN = "test-sim-token-0123456789abc"
ADMIN_TOKEN = "test-admin-token-0123456789"

# Deterministic fixture: the class is encoded by tilt_deg ranges, everything else is noise.
TILT_FOR_CLASS = {"SAFE": 0.05, "WATCH": 1.5, "WARNING": 4.0, "CRITICAL": 12.0}
TILT_RANGES = {"SAFE": (0.0, 0.3), "WATCH": (1.0, 2.0), "WARNING": (3.0, 5.0), "CRITICAL": (8.0, 20.0)}


def fixture_dataset(n_sessions: int = 12, per_class: int = 25, seed: int = 0) -> pd.DataFrame:
    """TEST FIXTURE ONLY - not data, not a model of physics."""
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_sessions):
        for label, (lo, hi) in TILT_RANGES.items():
            for _ in range(per_class):
                row = {f: float(rng.uniform(0, 0.5)) for f in FEATURE_NAMES}
                row["tilt_deg"] = float(rng.uniform(lo, hi))
                row["water_wet_duration_s"] = float(rng.uniform(0, 100))
                row["vib_events_30s"] = float(rng.integers(0, 5))
                row.update(session_id=f"fx-{s:02d}", label=label)
                rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture(scope="session")
def fixture_artifacts(tmp_path_factory) -> Path:
    """Artifacts folder with one active test_fixture model trained by the real pipeline."""
    from ml.train import train

    root = tmp_path_factory.mktemp("artifacts")
    csv = root / "fixture.csv"
    fixture_dataset().to_csv(csv, index=False)
    train("synthetic", db_path=root / "none.db", synthetic_path=csv, artifacts=root, seed=1, activate=True,
          version="rf-test-fixture", quiet=True, cv_splits=2, n_estimators=25, provenance_override="test_fixture")
    return root


@pytest.fixture
def settings(tmp_path, fixture_artifacts) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        backup_dir=tmp_path / "backups",
        log_dir=None,
        artifacts_dir=fixture_artifacts,
        frontend_dir=REPO / "frontend",
        device_tokens={"esp32-node-1": DEVICE_TOKEN, "sim-node-1": SIM_TOKEN},
        admin_token=ADMIN_TOKEN,
        demo_mode=False,
        allow_test_fixture_models=True,
        device_offline_s=10,
        prediction_fresh_s=10,
    )


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient

    from backend.main import create_app

    with TestClient(create_app(settings)) as c:
        yield c


DEV_HEADERS = {"X-Device-Id": "esp32-node-1", "X-Device-Token": DEVICE_TOKEN}

BASE_PAYLOAD = {
    "schema": "2.0", "feature_schema": "fs-1", "origin": "device", "device_id": "esp32-node-1", "fw": "4.0.0",
    "boot_id": "a1b2c3d4", "session_id": "a1b2c3d4-01", "reading_id": "a1b2c3d4-01-000001", "seq": 1,
    "uptime_ms": 60000, "epoch_ms": None, "replay": False, "op_state": "MONITORING", "rssi": -60, "faults": [],
    "buffer": {"queued": 0, "dropped": 0},
    "cal": {"status": "OK", "trigger": "boot", "error": None, "attempts": 1, "completed_uptime_ms": 5000,
            "gravity0": [0.1, 0.0, 0.995], "gyro_bias_dps": [0.1, -0.2, 0.05], "incl0_deg": 5.7,
            "accel_std_g": 0.002, "gyro_std_dps": 0.05, "vib_bg_eps": 0.0, "soil_raw0": 3000, "water_raw0": 0,
            "prev_incl0_deg": None, "prev_shift_deg": None, "nvs_saved": True, "mount_axis": "+Z", "samples": 200},
    "soil": {"raw": 3000, "filt": 3000.0, "do": 1, "moisture": 0.344, "pct": 34.4, "rate_per_min": 0.001,
             "mean_30s": 0.34, "std_30s": 0.002, "health": "OK", "flags": []},
    "water": {"raw": 0, "filt": 0.0, "wetness": 0.0, "pct": 0.0, "wet_s": 0.0, "rate_per_min": 0.0, "mean_30s": 0.0,
              "health": "OK", "flags": ["AT_DRY_ENDPOINT"]},
    "mpu": {"ax": 0.1, "ay": 0.0, "az": 0.99, "gx": 0.01, "gy": 0.0, "gz": 0.0, "incl0_deg": 5.7, "incl_deg": 5.75,
            "tilt_deg": 0.05, "incl_change_deg": 0.05, "roll_deg": 0.0, "pitch_deg": -5.7, "tilt_rate_dpm": 0.01,
            "acc_mag_g": 1.0, "acc_dyn_g": 0.002, "gyro_mag_dps": 0.08, "temp_c": 26.0, "health": "OK", "flags": []},
    "vib": {"do": 0, "transitions": 0, "events": 0, "window_ms": 2000, "eps": 0.0, "bg_eps": 0.0, "above_bg_eps": 0.0,
            "ev_5s": 0, "ev_10s": 0, "ev_30s": 0, "trend_eps": 0.0, "health": "OK"},
}


def make_payload(seq: int = 1, *, session: str = "a1b2c3d4-01", boot: str = "a1b2c3d4", tilt: float | None = 0.05,
                 **overrides) -> dict:
    p = copy.deepcopy(BASE_PAYLOAD)
    p.update(seq=seq, boot_id=boot, session_id=session, reading_id=f"{session}-{seq:06d}", uptime_ms=60000 + seq * 2000)
    p["mpu"]["tilt_deg"] = tilt
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(p.get(key), dict):
            p[key].update(value)
        else:
            p[key] = value
    return p
