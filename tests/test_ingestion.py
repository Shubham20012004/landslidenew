"""Telemetry ingestion, prediction eligibility, device responses and transitions."""
from __future__ import annotations

import json

import pytest

from conftest import ADMIN_TOKEN, DEV_HEADERS, SIM_TOKEN, TILT_FOR_CLASS, make_payload


def post(client, payload, headers=DEV_HEADERS):
    return client.post("/api/readings", content=json.dumps(payload), headers={**headers, "Content-Type": "application/json"})


def test_health_and_model_ready(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["database"]["schema_version"] == 2
    assert h["model"]["ready"] and h["model"]["provenance"] == "test_fixture"


def test_valid_reading_returns_prediction_and_is_stored(client):
    r = post(client, make_payload(1))
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "success" and body["prediction_available"] is True
    assert body["risk_state"] == "SAFE" and body["prediction_source"] == "random_forest"
    assert body["device_id"] == "esp32-node-1" and body["session_id"] == "a1b2c3d4-01"
    assert body["reading_id"] == "a1b2c3d4-01-000001" and body["model_version"] == "rf-test-fixture"
    assert abs(sum(body["probabilities"].values()) - 1) < 1e-6
    assert 0 <= body["confidence"] <= 1 and body["predicted_at"].endswith("Z")
    latest = client.get("/api/readings/latest").json()["reading"]
    assert latest["reading_id"] == "a1b2c3d4-01-000001"
    assert latest["prediction"]["risk_state"] == "SAFE" and latest["sensors"]["soil"]["raw"] == 3000


@pytest.mark.parametrize("label", ["SAFE", "WATCH", "WARNING", "CRITICAL"])
def test_all_four_class_mappings(client, label):
    """Test-fixture model: the class is encoded by tilt (see conftest). Not a real model."""
    body = post(client, make_payload(1, tilt=TILT_FOR_CLASS[label])).json()
    assert body["prediction_available"] and body["risk_state"] == label


def test_authentication_required(client):
    assert post(client, make_payload(1), headers={}).status_code == 401
    assert post(client, make_payload(1), headers={**DEV_HEADERS, "X-Device-Token": "wrong-token-xxxxxxxx"}).status_code == 401
    # authenticated as the simulator but claiming to be the ESP32
    r = post(client, make_payload(1), headers={"X-Device-Id": "sim-node-1", "X-Device-Token": SIM_TOKEN})
    assert r.status_code == 403
    kinds = [e["kind"] for e in client.get("/api/events?category=communication").json()["items"]]
    assert "AUTH_REJECTED" in kinds


@pytest.mark.parametrize("mutate,field", [
    (lambda p: p.update(schema="3.0"), "schema"),
    (lambda p: p["soil"].update(moisture=1.5), "soil.moisture"),
    (lambda p: p["mpu"].update(tilt_rate_dpm=50000), "mpu.tilt_rate_dpm"),
    (lambda p: p.pop("water"), "water"),
    (lambda p: p.update(reading_id="bad id!"), "reading_id"),
])
def test_invalid_telemetry_rejected_with_details(client, mutate, field):
    p = make_payload(1)
    mutate(p)
    r = post(client, p)
    assert r.status_code == 422
    assert any(d["loc"].startswith(field) for d in r.json()["error"]["details"])


def test_large_valid_tilt_rate_is_accepted_not_clipped(client):
    """The old backend rejected tilt rates > 1000 deg/min; a fast real movement is valid."""
    p = make_payload(1, tilt=12.0, mpu={"tilt_rate_dpm": 4500.0})
    r = post(client, p)
    assert r.status_code == 201
    stored = client.get("/api/readings/latest").json()["reading"]
    assert stored["features"]["tilt_rate_deg_per_min"] == 4500.0


def test_nan_is_rejected(client):
    raw = json.dumps(make_payload(1)).replace('"moisture": 0.344', '"moisture": NaN')
    r = client.post("/api/readings", content=raw, headers={**DEV_HEADERS, "Content-Type": "application/json"})
    assert r.status_code == 400


def test_duplicate_reading_is_not_stored_twice(client):
    first = post(client, make_payload(1)).json()
    second = post(client, make_payload(1))
    assert second.status_code == 200 and second.json()["status"] == "duplicate"
    assert second.json()["risk_state"] == first["risk_state"]
    hist = client.get("/api/readings/history?with_total=true").json()
    assert hist["total"] == 1


def test_out_of_order_and_replayed_readings_do_not_replace_live_state(client):
    post(client, make_payload(5, tilt=TILT_FOR_CLASS["CRITICAL"]))
    r_old = post(client, make_payload(3, tilt=TILT_FOR_CLASS["SAFE"])).json()
    assert r_old["is_live"] is False and r_old["risk_state"] == "SAFE"   # still classified for history
    r_replay = post(client, make_payload(9, tilt=TILT_FOR_CLASS["SAFE"], replay=True)).json()
    assert r_replay["is_live"] is False
    latest = client.get("/api/readings/latest").json()["reading"]
    assert latest["seq"] == 5 and latest["prediction"]["risk_state"] == "CRITICAL"
    assert client.get("/api/readings/history?with_total=true").json()["total"] == 3


def test_new_boot_is_live_even_with_lower_seq(client):
    post(client, make_payload(50))
    body = post(client, make_payload(1, boot="ffff0000", session="ffff0000-01")).json()
    assert body["is_live"] is True
    assert client.get("/api/readings/latest").json()["reading"]["session_id"] == "ffff0000-01"


def test_missing_features_give_insufficient_data_never_safe(client):
    p = make_payload(1, vib={"ev_30s": None}, soil={"rate_per_min": None})
    body = post(client, p).json()
    assert body["prediction_available"] is False and body["risk_state"] is None
    assert body["prediction_status"] == "INSUFFICIENT_DATA"
    assert "vib_events_30s" in body["reason"] and "soil_rate_per_min" in body["reason"]


def test_calibrating_and_sensor_fault_statuses(client):
    cal = make_payload(1, op_state="CALIBRATING", cal={"status": "IN_PROGRESS"}, mpu={"health": "NOT_CALIBRATED", "tilt_deg": None})
    assert post(client, cal).json()["prediction_status"] == "CALIBRATING"
    fault = make_payload(2, soil={"health": "FAULT", "moisture": None, "flags": ["ADC_LOW_RAIL"]}, faults=["SOIL_SENSOR_FAULT"])
    body = post(client, fault).json()
    assert body["prediction_status"] == "SENSOR_FAULT" and "soil" in body["reason"] and body["risk_state"] is None
    failed = make_payload(3, op_state="CALIBRATION_FAILED", cal={"status": "FAILED", "error": "MPU_NOT_DETECTED"})
    body = post(client, failed).json()
    assert body["prediction_status"] == "SENSOR_FAULT" and "MPU_NOT_DETECTED" in body["reason"]


def test_suspect_sensor_is_classified_but_marked_degraded(client):
    body = post(client, make_payload(1, soil={"health": "SUSPECT", "flags": ["ADC_DRY_SATURATION", "DO_DISAGREES"]})).json()
    assert body["prediction_available"] and body["data_quality"] == "DEGRADED"


def test_dry_saturated_soil_is_still_a_valid_measurement(client):
    """ADC 4095 at the dry end must not make the soil measurement unavailable."""
    p = make_payload(1, soil={"raw": 4095, "filt": 4095.0, "moisture": 0.0, "pct": 0.0, "flags": ["ADC_DRY_SATURATION"]})
    body = post(client, p).json()
    assert body["prediction_available"] is True


def test_feature_schema_mismatch_reports_model_not_ready(client):
    body = post(client, make_payload(1, feature_schema="fs-9")).json()
    assert body["prediction_status"] == "MODEL_NOT_READY" and "fs-9" in body["reason"]


def test_model_not_ready_when_provenance_not_allowed(settings):
    from fastapi.testclient import TestClient

    from backend.main import create_app

    settings.allow_test_fixture_models = False
    with TestClient(create_app(settings)) as c:
        body = post(c, make_payload(1)).json()
        assert body["prediction_status"] == "MODEL_NOT_READY" and body["risk_state"] is None
        assert c.get("/api/model/status").json()["ready"] is False
        # the reading itself is stored
        assert c.get("/api/readings/history?with_total=true").json()["total"] == 1


def test_prediction_storage_failure_keeps_the_reading(client, monkeypatch):
    from backend import repositories

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(repositories, "insert_prediction", boom)
    body = post(client, make_payload(1)).json()
    assert body["prediction_available"] is False and body["prediction_status"] == "INFERENCE_ERROR"
    monkeypatch.undo()
    assert client.get("/api/readings/history?with_total=true").json()["total"] == 1


def test_session_calibration_and_transition_events(client):
    post(client, make_payload(1, op_state="CALIBRATING", cal={"status": "IN_PROGRESS"},
                              mpu={"health": "NOT_CALIBRATED", "tilt_deg": None}))
    post(client, make_payload(2, tilt=TILT_FOR_CLASS["SAFE"]))
    post(client, make_payload(3, tilt=TILT_FOR_CLASS["WARNING"]))
    post(client, make_payload(4, tilt=TILT_FOR_CLASS["CRITICAL"], faults=["BACKEND_UNREACHABLE"]))
    post(client, make_payload(5, tilt=TILT_FOR_CLASS["CRITICAL"]))
    events = client.get("/api/events?limit=50").json()["items"][::-1]
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "SESSION_STARTED"
    assert "CALIBRATION_OK" in kinds and "PREDICTION_AVAILABLE" in kinds
    risk = [(e["from_state"], e["to_state"]) for e in events if e["kind"] == "RISK_CHANGED"]
    assert risk == [(None, "SAFE"), ("SAFE", "WARNING"), ("WARNING", "CRITICAL")]
    crit = [e for e in events if e["to_state"] == "CRITICAL"][0]
    assert crit["severity"] == "critical"
    assert kinds.count("FAULT_RAISED") == 1 and kinds.count("FAULT_CLEARED") == 1
    session = client.get("/api/sessions/a1b2c3d4-01").json()
    assert session["calibration_status"] == "OK" and session["reading_count"] == 5
    assert session["calibration"]["incl0_deg"] == 5.7


def test_device_offline_detection(client):
    import time as _t

    post(client, make_payload(1))
    svc = client.app.state.services
    with svc.db.transaction() as conn:
        conn.execute("UPDATE devices SET last_seen = ?", (_t.time() - 60,))
    published = svc.ingestion.check_offline()
    assert published and published[0]["kind"] == "DEVICE_OFFLINE"
    assert svc.ingestion.check_offline() == []           # reported once
    post(client, make_payload(2))
    kinds = [e["kind"] for e in client.get("/api/events?category=communication").json()["items"]]
    assert kinds[:2] == ["DEVICE_ONLINE", "DEVICE_OFFLINE"]
    assert client.get("/api/devices").json()["items"][0]["online"] is True


def test_request_size_limit(client):
    big = make_payload(1)
    big["padding"] = "x" * 70000
    assert post(client, big).status_code == 413


def test_dashboard_served_with_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "Landslide Monitor" in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/vendor/chart.umd.min.js").status_code == 200
    assert client.get("/api/health").headers["cache-control"] == "no-store"


def test_admin_reload(client):
    assert client.post("/api/model/reload").status_code == 401
    r = client.post("/api/model/reload", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert r.status_code == 200 and r.json()["ready"] is True
