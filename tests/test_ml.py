"""Training pipeline, dataset validation, leakage control, registry and inference."""
from __future__ import annotations

import json
import shutil

import numpy as np
import pandas as pd
import pytest

from conftest import TILT_FOR_CLASS, fixture_dataset
from ml import datasets
from ml.features import FEATURE_NAMES, extract_features, missing_features
from ml.predict import ModelBundle
from ml.registry import Registry, RegistryError
from ml.synthetic import generate
from ml.train import TrainingError, grouped_holdout, train


def test_feature_extraction_keeps_missing_as_none(conftest_payload=None):
    from conftest import make_payload

    p = make_payload(1)
    p["vib"]["ev_30s"] = None
    p["soil"]["moisture"] = float("nan")
    f = extract_features(p)
    assert list(f) == FEATURE_NAMES
    assert f["vib_events_30s"] is None and f["soil_moisture"] is None   # never zero-filled
    assert set(missing_features(f)) == {"vib_events_30s", "soil_moisture"}


def test_validation_and_cleaning_drops_bad_rows():
    df = fixture_dataset(n_sessions=2, per_class=5)
    df.loc[0, "soil_moisture"] = np.nan
    df.loc[1, "soil_moisture"] = 7.0                  # out of physical range
    df.loc[2, "label"] = "DANGER"
    df = pd.concat([df, df.iloc[[10]]])               # exact duplicate
    clean, rep = datasets.validate_and_clean(df, "test", "fixture", "x")
    assert rep.dropped_missing == 1 and rep.dropped_out_of_range == 1
    assert rep.dropped_bad_label == 1 and rep.dropped_duplicates == 1
    assert len(clean) == rep.rows_used == len(df) - 4
    with pytest.raises(ValueError):
        datasets.validate_and_clean(df.drop(columns=["tilt_deg"]), "test", "fixture", "x")


def test_sufficiency_check_reports_actual_deficiency():
    df = fixture_dataset(n_sessions=2, per_class=5)
    _, rep = datasets.validate_and_clean(df, "test", "fixture", "x")
    problems = datasets.sufficiency_problems(rep)
    assert any("labelled sessions" in p for p in problems) and any("readings" in p for p in problems)


def test_grouped_split_has_no_session_leakage():
    df = fixture_dataset(n_sessions=10, per_class=10)
    tr, te, _ = grouped_holdout(df, seed=3)
    assert not set(df.iloc[tr]["session_id"]) & set(df.iloc[te]["session_id"])
    assert set(df.iloc[te]["label"]) == {"SAFE", "WATCH", "WARNING", "CRITICAL"}


def test_identifiers_are_not_model_inputs(fixture_artifacts):
    meta = Registry(fixture_artifacts).read_metadata("rf-test-fixture")
    assert meta["features"] == FEATURE_NAMES
    assert not {"session_id", "reading_id", "label", "received_at", "t_s"} & set(meta["features"])


def test_metrics_are_genuine_and_complete(fixture_artifacts):
    m = Registry(fixture_artifacts).read_metrics("rf-test-fixture")
    t = m["test"]
    cm = np.array(t["confusion_matrix"]["matrix"])
    assert cm.sum() == t["n"] == m["split"]["test_rows"]
    assert abs(np.trace(cm) / cm.sum() - t["accuracy"]) < 1e-9
    assert set(m["feature_importance"]["impurity"]) == set(FEATURE_NAMES)
    assert m["majority_class_baseline"]["macro"]["f1"] < t["macro"]["f1"]
    assert m["provenance"] == "test_fixture"


def test_training_is_reproducible(tmp_path):
    csv = tmp_path / "fx.csv"
    fixture_dataset(n_sessions=10, per_class=12, seed=5).to_csv(csv, index=False)
    kw = dict(db_path=tmp_path / "none.db", synthetic_path=csv, seed=7, activate=False, quiet=True, cv_splits=2,
              n_estimators=15, provenance_override="test_fixture")
    a = train("synthetic", artifacts=tmp_path / "a", version="m1", **kw)
    b = train("synthetic", artifacts=tmp_path / "b", version="m1", **kw)
    assert a["metrics"]["test"] == b["metrics"]["test"]
    reg_a, reg_b = Registry(tmp_path / "a"), Registry(tmp_path / "b")
    rows = fixture_dataset(n_sessions=1, per_class=3, seed=99)
    for _, row in rows.iterrows():
        f = row[FEATURE_NAMES].to_dict()
        pa = ModelBundle.load(reg_a, "m1").predict(f)
        pb = ModelBundle.load(reg_b, "m1").predict(f)
        assert pa.probabilities == pb.probabilities


def test_training_refuses_insufficient_data(tmp_path):
    csv = tmp_path / "fx.csv"
    fixture_dataset(n_sessions=2, per_class=5).to_csv(csv, index=False)
    with pytest.raises(TrainingError, match="INSUFFICIENT_DATA"):
        train("synthetic", db_path=tmp_path / "none.db", synthetic_path=csv, artifacts=tmp_path / "a", seed=1,
              activate=True, quiet=True)
    assert not (tmp_path / "a").exists() or not Registry(tmp_path / "a").list_versions()


def test_experimental_source_without_labels_reports_insufficient_data(tmp_path, settings):
    from backend.database import Database

    db = Database(settings.db_path)
    db.migrate()
    db.close()
    with pytest.raises(TrainingError, match="INSUFFICIENT_DATA"):
        train("experimental", db_path=settings.db_path, synthetic_path=tmp_path / "x.csv", artifacts=tmp_path / "a",
              seed=1, activate=True, quiet=True)


def test_registry_integrity_activation_and_rollback(fixture_artifacts, tmp_path):
    root = tmp_path / "art"
    shutil.copytree(fixture_artifacts, root)
    reg = Registry(root)
    shutil.copytree(root / "rf-test-fixture", root / "rf-test-two")
    meta = json.loads((root / "rf-test-two" / "metadata.json").read_text())
    meta["model_version"] = "rf-test-two"
    (root / "rf-test-two" / "metadata.json").write_text(json.dumps(meta))
    reg.activate("rf-test-two")
    assert reg.read_index()["active"] == "rf-test-two" and reg.read_index()["previous"] == "rf-test-fixture"
    reg.rollback()
    assert reg.read_index()["active"] == "rf-test-fixture"
    # tampering is detected and the model is refused
    with (root / "rf-test-two" / "model.joblib").open("ab") as fh:
        fh.write(b"tampered")
    with pytest.raises(RegistryError, match="hash mismatch"):
        reg.activate("rf-test-two")
    assert reg.read_index()["active"] == "rf-test-fixture"
    with pytest.raises(RegistryError):
        reg.artifact_dir("../escape")


def test_model_bundle_rejects_feature_order_mismatch(fixture_artifacts, tmp_path):
    from ml.registry import sha256_file

    root = tmp_path / "art"
    shutil.copytree(fixture_artifacts, root)
    meta_path = root / "rf-test-fixture" / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["features"] = list(reversed(meta["features"]))
    meta["artifact_sha256"] = sha256_file(root / "rf-test-fixture" / "model.joblib")
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(RegistryError, match="order"):
        ModelBundle.load(Registry(root), "rf-test-fixture")


def test_inference_requires_all_features(fixture_artifacts):
    bundle = ModelBundle.load(Registry(fixture_artifacts), "rf-test-fixture")
    f = {n: 0.1 for n in FEATURE_NAMES}
    f["tilt_deg"] = TILT_FOR_CLASS["CRITICAL"]
    p = bundle.predict(f)
    assert p.risk_state == "CRITICAL" and abs(sum(p.probabilities.values()) - 1) < 1e-9
    f["tilt_deg"] = None
    with pytest.raises(ValueError, match="tilt_deg"):
        bundle.predict(f)


def test_synthetic_generator_is_reproducible_and_complete():
    a, b = generate(n_sessions=6, seed=11), generate(n_sessions=6, seed=11)
    pd.testing.assert_frame_equal(a, b)
    assert not a[FEATURE_NAMES].isna().any().any()
    _, rep = datasets.validate_and_clean(a, "synthetic", "gen", "x")
    assert rep.dropped_out_of_range == 0 and rep.rows_used == len(a)
