"""ML pipeline smoke test (quick grid, temporary output folder)."""
import json
from pathlib import Path

import joblib
import pytest

from ml import train_model

CSV = train_model.DEFAULT_CSV


@pytest.mark.skipif(not CSV.exists(), reason="synthetic CSV not present")
def test_training_pipeline_quick(tmp_path):
    m = train_model.main(["--quick", "--out", str(tmp_path)])
    assert m["DATA_STATUS"].startswith("SYNTHETIC")
    assert not m["split"]["overlap_experiments"]                     # group split: no experiment on both sides
    assert set(m["features"]) <= {"moisture", "rain", "tilt_change_abs_deg", "moisture_rate_per_min", "tilt_rate_deg_per_min"}
    t = m["test_metrics_grouped_holdout"]
    for k in ("accuracy", "macro_precision", "macro_recall", "macro_f1", "per_class_recall", "critical_recall",
              "confusion_matrix", "ordinal_error_distance"):
        assert k in t
    assert sum(map(sum, t["confusion_matrix"]["rows_true_cols_pred"])) == t["n"]
    assert "WARNING" in m["leakage_comparison_random_row_split"]
    for f in ("metrics.json", "rf_model.joblib", "confusion_matrix.png", "feature_importance.png"):
        assert (tmp_path / f).exists(), f
    bundle = joblib.load(tmp_path / "rf_model.joblib")
    assert bundle["training_data"].startswith("SYNTHETIC")
    saved = json.loads((tmp_path / "metrics.json").read_text())
    assert saved["dataset"]["profile"]["experiments"] == 50
