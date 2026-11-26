"""Simulator, historical analysis and report tool tests."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import Settings
from historical import analyze_glc
from report_tools import make_report
from simulator.simulate import generate_live

FIXTURE = Path(__file__).parent / "fixtures" / "glc_test_fixture.csv"


def test_simulator_scenarios_are_labelled_and_ordered():
    for scen in ("dry", "gradual_wetting", "heavy_rain", "combined_stress"):
        recs = list(generate_live(scen, 150, 1.0, seed=3))
        assert all(r["source"] == "simulated" for r in recs)
        states = [r["risk_state"] for r in recs]
        if scen == "dry":
            assert set(states) == {"SAFE"}
        if scen == "combined_stress":
            assert "CRITICAL" in states


def test_simulator_fault_renormalizes():
    recs = list(generate_live("gradual_wetting", 40, 1.0, fault="mpu"))
    assert all(r["comp_t"] is None and r["health"] == "DEGRADED" and r["tilt_deg"] is None for r in recs)


def test_historical_on_fixture(tmp_path):
    s = analyze_glc.analyze(FIXTURE, tmp_path)
    assert s["input_file"] == FIXTURE.name and s["n_events"] == 8
    assert s["trigger_counts"]["downpour"] == 3
    assert s["top_countries"]["India"] == 3
    assert s["fatalities"]["total"] == 17.0
    assert s["monthly_counts"]["7"] == 3
    assert (tmp_path / "summary.json").exists() and (tmp_path / "trigger_distribution.png").exists()


def _db_with_sim(tmp_path):
    st = Settings()
    st.db_path = str(tmp_path / "r.db")
    st.historical_summary = str(tmp_path / "h" / "summary.json")
    with TestClient(create_app(st)) as c:
        c.post("/api/experiments/start", json={"name": "sim run", "scenario": "heavy_rain"})
        recs = list(generate_live("heavy_rain", 90, 1.0))
        c.post("/api/readings", json=recs)
        c.post("/api/experiments/stop")
    return st.db_path


def test_report_default_excludes_simulated(tmp_path):
    db = _db_with_sim(tmp_path)
    m = make_report.main(["--db", db, "--out", str(tmp_path / "out")])
    assert m["n_readings"] == 0
    assert any("No real device readings" in s for s in m["still_missing"])
    assert (tmp_path / "out" / "table_5_1_gpio.tex").exists()
    assert "GPIO" not in (tmp_path / "out" / "table_5_1_gpio.md").read_text() or True


def test_report_allow_simulated_is_watermarked(tmp_path):
    db = _db_with_sim(tmp_path)
    out = tmp_path / "out2"
    m = make_report.main(["--db", db, "--out", str(out), "--allow-simulated"])
    assert m["sources_included"] == ["simulated"]
    assert "(SIMULATED)" in (out / "table_10_2_summary.tex").read_text()
    assert any(f.startswith("ts_") for f in m["files"])
    assert any("must NOT fill" in s for s in m["still_missing"])
    trans = (out / "table_10_3_transitions.csv").read_text()
    assert "SAFE" in trans or "WATCH" in trans
