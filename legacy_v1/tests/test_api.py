"""API endpoint tests using a temporary SQLite database."""
import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import Settings

REPO = Path(__file__).resolve().parents[1]


def reading(**kw):
    base = {"device_id": "esp32-test", "seq": 1, "source": "device", "soil_raw": 2100, "rain_raw": 3900,
            "moisture": 0.4, "rain": 0.1, "rain_duration_s": 0, "tilt_deg": 0.2, "moisture_rate_per_min": 0.001,
            "tilt_rate_deg_per_min": 0.0, "comp_m": 0.0, "comp_r": 0.0, "comp_t": 0.0, "risk_score": 0.1,
            "risk_state": "SAFE", "raw_state": "SAFE", "health": "OK", "soil_status": "OK", "rain_status": "OK",
            "mpu_status": "OK", "faults": [], "rates_ready": True}
    base.update(kw)
    return base


@pytest.fixture
def client(tmp_path):
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.historical_summary = str(tmp_path / "hist" / "summary.json")
    s.telegram_enabled = False
    app = create_app(s)
    with TestClient(app) as c:
        yield c


def test_health(client):
    j = client.get("/api/health").json()
    assert j["status"] == "ok" and j["port"] == 8000
    assert j["esp32_post_url"].endswith(":8000/api/readings")
    assert j["telegram_enabled"] is False


def test_post_single_and_latest(client):
    r = client.post("/api/readings", json=reading())
    assert r.status_code == 200 and r.json()["stored"] == 1
    j = client.get("/api/latest?source=device").json()
    assert j["reading"]["risk_state"] == "SAFE" and j["authoritative_state"] == "SAFE"
    assert j["reading"]["server_time"] > 0 and j["reading"]["source"] == "device"


def test_post_batch_in_order_and_history(client):
    batch = [reading(seq=i, risk_score=0.1 * i, risk_state=s)
             for i, s in zip(range(1, 5), ["SAFE", "WATCH", "WATCH", "WARNING"])]
    assert client.post("/api/readings", json=batch).json()["stored"] == 4
    h = client.get("/api/history?minutes=5").json()
    assert [r["seq"] for r in h["readings"]] == [1, 2, 3, 4]


def test_validation_rejects_bad_payload(client):
    assert client.post("/api/readings", json=reading(moisture=1.7)).status_code == 422
    assert client.post("/api/readings", json=reading(risk_state="DANGER")).status_code == 422
    assert client.post("/api/readings", json=reading(source="other")).status_code == 422
    assert client.post("/api/readings", json=[]).status_code in (400, 422)


def test_null_values_for_uncalibrated_sensors(client):
    r = reading(moisture=None, rain=None, tilt_deg=None, comp_m=None, comp_r=None, comp_t=None, risk_score=None,
                risk_state="UNKNOWN", health="FAULT", soil_status="UNCALIBRATED", rain_status="FAULT",
                mpu_status="FAULT", faults=["SOIL_UNCALIBRATED", "RAIN_RAIL_LOW", "MPU_I2C_ERROR", "NO_USABLE_COMPONENTS"])
    j = client.post("/api/readings", json=r).json()
    assert j["rule_state"] == "UNKNOWN" and j["ml_state"] is None
    latest = client.get("/api/latest").json()["reading"]
    assert latest["moisture"] is None and "RAIN_RAIL_LOW" in latest["faults"]


def test_events_state_changes_and_faults(client):
    client.post("/api/readings", json=reading(seq=1))
    client.post("/api/readings", json=reading(seq=2, risk_state="WARNING", risk_score=0.6, faults=["MPU_I2C_ERROR"]))
    client.post("/api/readings", json=reading(seq=3, risk_state="WARNING", risk_score=0.6, faults=[]))
    ev = client.get("/api/events").json()["events"]
    kinds = [(e["kind"], e.get("to_state"), e.get("detail")) for e in ev]
    assert ("STATE_CHANGE", "WARNING", "health=OK") in kinds
    assert any(k == "FAULT_ON" and d == "MPU_I2C_ERROR" for k, _, d in kinds)
    assert any(k == "FAULT_OFF" and d == "MPU_I2C_ERROR" for k, _, d in kinds)


def test_first_reading_of_stream_creates_events(client):
    client.post("/api/readings", json=reading(device_id="new-node", risk_state="WATCH", faults=["MPU_I2C_ERROR"]))
    ev = [e for e in client.get("/api/events").json()["events"] if e["device_id"] == "new-node"]
    kinds = {(e["kind"], e.get("to_state"), e.get("detail")) for e in ev}
    assert ("STATE_CHANGE", "WATCH", "health=OK") in kinds
    assert ("FAULT_ON", None, "MPU_I2C_ERROR") in kinds


def test_experiments_and_export(client):
    assert client.post("/api/experiments/stop").status_code == 409
    e = client.post("/api/experiments/start", json={"name": "run1", "scenario": "gradual_wetting"}).json()["experiment"]
    client.post("/api/readings", json=reading(seq=1))
    client.post("/api/readings", json=reading(seq=2, source="simulated", device_id="sim"))
    assert client.post("/api/experiments/stop").status_code == 200
    client.post("/api/readings", json=reading(seq=3))
    rows = list(csv.DictReader(io.StringIO(client.get("/api/export.csv?source=device").text)))
    assert len(rows) == 2 and all(r["source"] == "device" for r in rows)
    assert rows[0]["experiment_id"] == str(e["id"]) and rows[0]["scenario"] == "gradual_wetting"
    assert rows[1]["experiment_id"] == ""
    sim = list(csv.DictReader(io.StringIO(client.get(f"/api/export.csv?source=simulated").text)))
    assert len(sim) == 1 and sim[0]["source"] == "simulated"
    assert client.post("/api/experiments/start", json={"name": "x", "scenario": "bogus"}).status_code == 422


def test_source_filter_latest(client):
    client.post("/api/readings", json=reading(seq=1))
    client.post("/api/readings", json=reading(seq=2, source="simulated", device_id="sim", risk_state="CRITICAL"))
    assert client.get("/api/latest?source=device").json()["reading"]["risk_state"] == "SAFE"
    assert client.get("/api/latest?source=simulated").json()["reading"]["risk_state"] == "CRITICAL"


def test_config_and_dashboard(client):
    c = client.get("/api/config").json()
    assert c["values"]["TAU1"] < c["values"]["TAU2"] < c["values"]["TAU3"]
    assert c["provisional"]["TAU1"] is True and c["provisional"]["W_MOISTURE"] is False
    assert client.get("/").status_code == 200
    assert client.get("/static/vendor/chart.umd.min.js").status_code == 200
    assert client.get("/api/historical/summary").json()["available"] is False


@pytest.mark.skipif(not (REPO / "ml" / "outputs" / "rf_model.joblib").exists(), reason="model not trained yet")
def test_ml_advisory_and_disagreement(client):
    j = client.post("/api/readings", json=reading(moisture=0.95, rain=0.95, tilt_deg=4.5, moisture_rate_per_min=0.01,
                                                  tilt_rate_deg_per_min=0.06, risk_state="SAFE")).json()
    assert j["ml_state"] in ("SAFE", "WATCH", "WARNING", "CRITICAL")
    assert j["rule_state"] == "SAFE"  # rule state stays authoritative
    assert j["disagree"] == (j["ml_state"] != "SAFE")
    info = client.get("/api/ml/info").json()
    assert info["loaded"] and "SYNTHETIC" in info["meta"]["training_data"]
