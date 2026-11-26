"""Database rows -> API JSON."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from ml.features import CLASSES, FEATURE_NAMES

from .database import iso

SENSOR_KEYS = ("soil", "water", "mpu", "vib")


def _loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def prediction_api(p: Mapping | None, fresh_s: float, now: float) -> dict:
    if p is None:
        return {"status": "NOT_PROCESSED", "available": False, "reason": "no prediction stored for this reading"}
    ok = p["status"] == "OK"
    created = p["created_at"]
    return {
        "status": p["status"],
        "available": ok,
        "risk_state": p["risk_state"] if ok else None,
        "confidence": p["confidence"] if ok else None,
        "probabilities": {c: p[f"p_{c.lower()}"] for c in CLASSES} if ok else None,
        "model_version": p["model_version"],
        "model_provenance": p["model_provenance"],
        "demo_model": p["model_provenance"] == "synthetic_demo",
        "data_quality": p["data_quality"],
        "reason": p["reason"],
        "predicted_at": iso(created),
        "predicted_ts": created,
        "age_s": round(now - created, 1),
        "stale": (now - created) > fresh_s,
        "latency_ms": p["latency_ms"],
    }


def reading_api(r: sqlite3.Row | Mapping, p: Mapping | None, fresh_s: float, now: float) -> dict:
    payload = _loads(r["payload"], {})
    sensors = {k: payload.get(k) for k in SENSOR_KEYS}
    return {
        "pk": r["id"],
        "reading_id": r["reading_id"],
        "device_id": r["device_id"],
        "session_id": r["session_id"],
        "boot_id": r["boot_id"],
        "seq": r["seq"],
        "received_at": iso(r["received_at"]),
        "received_ts": r["received_at"],
        "device_uptime_ms": r["device_uptime_ms"],
        "is_live": bool(r["is_live"]),
        "is_replay": bool(r["is_replay"]),
        "origin": r["origin"],
        "fw_version": r["fw_version"],
        "op_state": r["op_state"],
        "calibration_status": r["calibration_status"],
        "rssi": r["rssi"],
        "faults": _loads(r["faults"], []),
        "buffer": payload.get("buffer"),
        "cal": payload.get("cal"),
        "sensors": sensors,
        "features": {f: r[f] for f in FEATURE_NAMES},
        "prediction": prediction_api(p, fresh_s, now),
    }


def page_row_api(row: sqlite3.Row) -> dict:
    """Compact table row for history listings (no payload)."""
    d = dict(row)
    out = {
        "pk": d["id"],
        "reading_id": d["reading_id"],
        "device_id": d["device_id"],
        "session_id": d["session_id"],
        "seq": d["seq"],
        "received_at": iso(d["received_at"]),
        "received_ts": d["received_at"],
        "is_live": bool(d["is_live"]),
        "is_replay": bool(d["is_replay"]),
        "origin": d["origin"],
        "op_state": d["op_state"],
        "faults": _loads(d["faults"], []),
        "soil_raw": d["soil_raw"],
        "water_raw": d["water_raw"],
        "incl_deg": d["incl_deg"],
        "health": {"soil": d["soil_health"], "water": d["water_health"], "mpu": d["mpu_health"]},
        "features": {f: d[f] for f in FEATURE_NAMES},
        "prediction": {
            "status": d["pred_status"],
            "risk_state": d["pred_risk_state"],
            "confidence": d["pred_confidence"],
            "model_version": d["pred_model_version"],
            "model_provenance": d["pred_model_provenance"],
            "reason": d["pred_reason"],
        },
    }
    return out


def event_api(e: sqlite3.Row | Mapping) -> dict:
    return {
        "id": e["id"],
        "created_at": iso(e["created_at"]),
        "created_ts": e["created_at"],
        "kind": e["kind"],
        "severity": e["severity"],
        "device_id": e["device_id"],
        "session_id": e["session_id"],
        "reading_pk": e["reading_pk"],
        "from_state": e["from_state"],
        "to_state": e["to_state"],
        "message": e["message"],
        "detail": _loads(e["detail"], None),
    }


def session_api(s: sqlite3.Row | Mapping) -> dict:
    return {
        "session_id": s["session_id"],
        "device_id": s["device_id"],
        "boot_id": s["boot_id"],
        "origin": s["origin"],
        "fw_version": s["fw_version"],
        "started_at": iso(s["first_seen"]),
        "started_ts": s["first_seen"],
        "last_seen_at": iso(s["last_seen"]),
        "last_seen_ts": s["last_seen"],
        "reading_count": s["reading_count"],
        "calibration_status": s["calibration_status"],
        "calibration_trigger": s["calibration_trigger"],
        "calibration_error": s["calibration_error"],
        "calibration": _loads(s["calibration_json"], None),
    }


def device_api(d: sqlite3.Row | Mapping, now: float, offline_s: float) -> dict:
    online = (now - d["last_seen"]) <= offline_s
    return {
        "device_id": d["device_id"],
        "origin": d["origin"],
        "first_seen_at": iso(d["first_seen"]),
        "last_seen_at": iso(d["last_seen"]),
        "last_seen_ts": d["last_seen"],
        "seconds_since_last_seen": round(now - d["last_seen"], 1),
        "online": online,
        "fw_version": d["fw_version"],
        "boot_id": d["boot_id"],
        "session_id": d["session_id"],
        "last_seq": d["last_seq"],
        "rssi": d["rssi"],
        "remote_addr": d["remote_addr"],
        "last_reading_pk": d["last_reading_pk"],
    }
