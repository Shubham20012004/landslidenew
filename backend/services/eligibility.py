"""Decides whether a reading may be classified by the Random Forest.

Operational conditions are reported separately from the four risk classes and
never fall back to SAFE.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from ml.features import SCHEMA_VERSION, missing_features, out_of_range

from ..schemas import TelemetryIn

OK = "OK"
CALIBRATING = "CALIBRATING"
SENSOR_FAULT = "SENSOR_FAULT"
MODEL_NOT_READY = "MODEL_NOT_READY"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
INFERENCE_ERROR = "INFERENCE_ERROR"
OPERATIONAL_STATUSES = (CALIBRATING, SENSOR_FAULT, MODEL_NOT_READY, INSUFFICIENT_DATA, INFERENCE_ERROR)


@dataclass
class Assessment:
    status: str
    reason: str | None
    data_quality: str  # OK | DEGRADED


def assess(t: TelemetryIn, features: Mapping[str, float | None], model_ready: bool, model_reason: str) -> Assessment:
    healths = {"soil": t.soil.health, "water": t.water.health, "mpu": t.mpu.health}
    quality = "DEGRADED" if "SUSPECT" in healths.values() else "OK"
    if t.op_state in ("INITIALIZING", "CALIBRATING"):
        return Assessment(CALIBRATING, "device calibration in progress", quality)
    if t.op_state == "CALIBRATION_FAILED":
        detail = t.cal.error if t.cal and t.cal.error else "unknown reason"
        return Assessment(SENSOR_FAULT, f"calibration failed: {detail}", quality)
    faulty = [name for name, h in healths.items() if h == "FAULT"]
    if faulty:
        return Assessment(SENSOR_FAULT, "sensor fault: " + ", ".join(faulty), quality)
    if t.mpu.health == "NOT_CALIBRATED":
        return Assessment(SENSOR_FAULT, "MPU6050 has no baseline in this session", quality)
    if t.feature_schema != SCHEMA_VERSION:
        return Assessment(
            MODEL_NOT_READY, f"feature schema {t.feature_schema} is not supported (backend expects {SCHEMA_VERSION})", quality
        )
    if not model_ready:
        return Assessment(MODEL_NOT_READY, model_reason, quality)
    missing = missing_features(features)
    if missing:
        return Assessment(INSUFFICIENT_DATA, "waiting for: " + ", ".join(missing), quality)
    bad = out_of_range(features)
    if bad:
        return Assessment(SENSOR_FAULT, "feature outside its physical range: " + ", ".join(bad), quality)
    return Assessment(OK, None, quality)
