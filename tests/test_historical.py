"""NASA Global Landslide Catalog analysis script on an 8-row TEST FIXTURE (not catalogue data)."""
import json
from pathlib import Path

from historical import analyze_glc

FIXTURE = Path(__file__).parent / "fixtures" / "glc_test_fixture.csv"


def test_glc_analysis_runs_on_fixture(tmp_path):
    rc = analyze_glc.main(["--input", str(FIXTURE), "--out", str(tmp_path)])
    assert rc in (0, None)
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["n_events"] == 8
