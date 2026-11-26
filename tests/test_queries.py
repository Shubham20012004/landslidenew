"""History, series, sessions, CSV export and experiment annotations."""
from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime, timedelta, timezone

from conftest import ADMIN_TOKEN, DEV_HEADERS, TILT_FOR_CLASS, make_payload


def post(client, payload):
    return client.post("/api/readings", content=json.dumps(payload), headers={**DEV_HEADERS, "Content-Type": "application/json"})


def seed(client, n=12):
    for i in range(1, n + 1):
        tilt = TILT_FOR_CLASS["SAFE"] if i <= n // 2 else TILT_FOR_CLASS["WARNING"]
        post(client, make_payload(i, tilt=tilt))


def test_history_keyset_pagination_and_filters(client):
    seed(client, 12)
    page1 = client.get("/api/readings/history?limit=5&with_total=true").json()
    assert page1["total"] == 12 and len(page1["items"]) == 5
    assert [r["seq"] for r in page1["items"]] == [12, 11, 10, 9, 8]
    page2 = client.get(f"/api/readings/history?limit=5&before={page1['next_before']}").json()
    assert [r["seq"] for r in page2["items"]] == [7, 6, 5, 4, 3]
    page3 = client.get(f"/api/readings/history?limit=5&before={page2['next_before']}").json()
    assert [r["seq"] for r in page3["items"]] == [2, 1] and page3["next_before"] is None
    warn = client.get("/api/readings/history?risk_state=WARNING&with_total=true").json()
    assert warn["total"] == 6 and all(r["prediction"]["risk_state"] == "WARNING" for r in warn["items"])
    none = client.get("/api/readings/history?session_id=zz-01&with_total=true").json()
    assert none["total"] == 0 and none["items"] == []
    assert client.get("/api/readings/history?risk_state=BAD").status_code == 422
    assert client.get("/api/readings/history?start=2030-01-01T00:00:00Z&end=2029-01-01T00:00:00Z").status_code == 422


def test_series_preserves_missing_values(client):
    post(client, make_payload(1))
    post(client, make_payload(2, soil={"health": "FAULT", "moisture": None}))
    pts = client.get("/api/readings/series").json()["points"]
    assert len(pts) == 2 and pts[1]["soil_moisture"] is None and pts[0]["soil_moisture"] == 0.344


def test_series_buckets_long_ranges(client):
    seed(client, 30)
    data = client.get("/api/readings/series?max_points=10&start=0").json()
    assert data["bucket_s"] is not None and len(data["points"]) <= 30


def test_csv_export_readings(client):
    seed(client, 4)
    r = client.get("/api/export/csv?session_id=a1b2c3d4-01")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 4 and rows[0]["reading_id"] == "a1b2c3d4-01-000001"
    assert rows[0]["pred_risk_state"] == "SAFE" and "payload" not in rows[0]


def test_sessions_listing(client):
    seed(client, 2)
    post(client, make_payload(1, boot="bbbb0000", session="bbbb0000-01"))
    items = client.get("/api/sessions").json()["items"]
    assert {s["session_id"] for s in items} == {"a1b2c3d4-01", "bbbb0000-01"}
    assert client.get("/api/sessions/nope-01").status_code == 404


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_annotation_workflow_and_labeled_export(client):
    seed(client, 6)
    now = time.time()
    body = {"device_id": "esp32-node-1", "session_id": "a1b2c3d4-01", "start": _iso(now - 60), "end": _iso(now + 5),
            "label": "WATCH", "notes": "wetted 100 ml at t=0"}
    assert client.post("/api/dataset/labels", json=body).status_code == 401
    r = client.post("/api/dataset/labels", json=body, headers={"X-Admin-Token": ADMIN_TOKEN})
    assert r.status_code == 201 and r.json()["readings_in_range"] == 6
    summary = client.get("/api/dataset/summary").json()
    assert summary["labeled_readings"] == 6 and summary["per_class"]["WATCH"]["readings"] == 6
    rows = list(csv.DictReader(io.StringIO(client.get("/api/export/csv?scope=labeled").text)))
    assert len(rows) == 6 and {r["label"] for r in rows} == {"WATCH"}
    # Labels are documented observations, independent of the model's own predictions (SAFE/WARNING here).
    # Overlapping conflicting label -> readings become ambiguous and leave the training set.
    body2 = {**body, "label": "CRITICAL"}
    aid = client.post("/api/dataset/labels", json=body2, headers={"X-Admin-Token": ADMIN_TOKEN}).json()["id"]
    summary = client.get("/api/dataset/summary").json()
    assert summary["ambiguous_readings"] == 6 and summary["labeled_readings"] == 0
    assert client.delete(f"/api/dataset/labels/{aid}", headers={"X-Admin-Token": ADMIN_TOKEN}).status_code == 200
    assert client.get("/api/dataset/summary").json()["labeled_readings"] == 6


def test_annotation_validation(client):
    seed(client, 2)
    now = datetime.now(timezone.utc)
    bad = {"device_id": "esp32-node-1", "start": now.isoformat(), "end": (now - timedelta(minutes=1)).isoformat(), "label": "SAFE"}
    assert client.post("/api/dataset/labels", json=bad, headers={"X-Admin-Token": ADMIN_TOKEN}).status_code == 422
    empty = {"device_id": "esp32-node-1", "start": "2020-01-01T00:00:00Z", "end": "2020-01-01T01:00:00Z", "label": "SAFE"}
    assert client.post("/api/dataset/labels", json=empty, headers={"X-Admin-Token": ADMIN_TOKEN}).status_code == 422
    unknown = {**empty, "device_id": "ghost"}
    assert client.post("/api/dataset/labels", json=unknown, headers={"X-Admin-Token": ADMIN_TOKEN}).status_code == 404


def test_simulated_readings_never_enter_training_data(client):
    from conftest import SIM_TOKEN

    p = make_payload(1, device_id="sim-node-1", origin="simulated")
    r = client.post("/api/readings", content=json.dumps(p),
                    headers={"X-Device-Id": "sim-node-1", "X-Device-Token": SIM_TOKEN, "Content-Type": "application/json"})
    assert r.status_code == 201
    now = time.time()
    client.post("/api/dataset/labels", headers={"X-Admin-Token": ADMIN_TOKEN}, json={
        "device_id": "sim-node-1", "start": _iso(now - 60), "end": _iso(now + 5), "label": "SAFE"})
    summary = client.get("/api/dataset/summary").json()
    assert summary["device_readings_total"] == 0 and summary["labeled_readings"] == 0


def test_model_metrics_are_read_from_the_artifact(client):
    m = client.get("/api/model/metrics").json()
    assert m["version"] == "rf-test-fixture" and m["provenance"] == "test_fixture"
    assert set(m["metrics"]["test"]["per_class"]) == {"SAFE", "WATCH", "WARNING", "CRITICAL"}
    status = client.get("/api/model/status").json()
    assert status["feature_schema_detail"]["version"] == "fs-1"
