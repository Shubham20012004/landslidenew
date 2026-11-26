"""Feature schema shared by the backend (live inference) and the training pipeline.

The ESP32 computes the features (it has the 50 Hz MPU6050 samples and the
SW-420 interrupt counts); this module maps a telemetry payload onto the
ordered feature vector defined in ``feature_schema.json``.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

SCHEMA_PATH = Path(__file__).with_name("feature_schema.json")


@lru_cache(maxsize=1)
def load_schema() -> dict:
    with SCHEMA_PATH.open(encoding="utf-8") as fh:
        schema = json.load(fh)
    names = [f["name"] for f in schema["features"]]
    if len(names) != len(set(names)):
        raise ValueError("duplicate feature names in feature_schema.json")
    return schema


SCHEMA = load_schema()
SCHEMA_VERSION: str = SCHEMA["version"]
CLASSES: list[str] = list(SCHEMA["classes"])
FEATURE_NAMES: list[str] = [f["name"] for f in SCHEMA["features"]]
FEATURE_SENSOR: dict[str, str] = {f["name"]: f["sensor"] for f in SCHEMA["features"]}
FEATURE_RANGE: dict[str, tuple[float, float]] = {f["name"]: (f["min"], f["max"]) for f in SCHEMA["features"]}
SENSORS = ("soil", "water", "mpu", "vib")


def _get_path(payload: Mapping[str, Any], path: str) -> Any:
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _clean(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def extract_features(payload: Mapping[str, Any]) -> dict[str, float | None]:
    """Ordered mapping feature-name -> value (None when unavailable).

    Missing values are never replaced by zero or any other default.
    """
    return {f["name"]: _clean(_get_path(payload, f["path"])) for f in SCHEMA["features"]}


def missing_features(features: Mapping[str, float | None]) -> list[str]:
    return [n for n in FEATURE_NAMES if features.get(n) is None]


def out_of_range(features: Mapping[str, float | None]) -> list[str]:
    bad = []
    for name, (lo, hi) in FEATURE_RANGE.items():
        v = features.get(name)
        if v is not None and not (lo <= v <= hi):
            bad.append(name)
    return bad
