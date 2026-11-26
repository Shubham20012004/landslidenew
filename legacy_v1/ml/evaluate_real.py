"""Evaluate the (synthetic-trained) Random Forest on REAL readings exported from SQLite.

    1. Run real experiments with the ESP32 and the backend.
    2. Export:  http://localhost:8000/api/export.csv?source=device   (save as real.csv)
    3. python ml/evaluate_real.py --csv real.csv
       python ml/evaluate_real.py --csv real.csv --label-column observed_state

Without --label-column the script only reports how often the ML class agrees
with the firmware's rule-based state. That is NOT validation: it compares two
classifiers, and the rule thresholds are themselves provisional.

With --label-column, labels must come from an independent source that you
added yourself (e.g. an observer's annotation per run). Only then is the
result an evaluation against those labels - still on a miniature lab model.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from ml.train_model import CLASSES, metrics_block  # noqa: E402

FIELD = {"moisture": "moisture", "rain": "rain", "tilt_change_abs_deg": "tilt_deg",
         "moisture_rate_per_min": "moisture_rate_per_min", "tilt_rate_deg_per_min": "tilt_rate_deg_per_min"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--model", default=str(REPO / "ml" / "outputs" / "rf_model.joblib"))
    ap.add_argument("--label-column", default=None)
    ap.add_argument("--allow-simulated", action="store_true", help="include source=simulated rows (labelled SIMULATED)")
    ap.add_argument("--out", default=str(REPO / "ml" / "outputs" / "real_eval"))
    args = ap.parse_args(argv)

    df = pd.read_csv(args.csv)
    if "source" not in df.columns:
        raise SystemExit("CSV has no 'source' column; use the backend's /api/export.csv output.")
    if not args.allow_simulated:
        df = df[df["source"] == "device"]
    if df.empty:
        raise SystemExit("No real device rows found. Nothing to evaluate (no claim of real-data validation is possible).")
    sources = sorted(df["source"].unique())
    bundle = joblib.load(args.model)
    feats = bundle["features"]
    X = pd.DataFrame({f: df[FIELD[f]] for f in feats})
    X["tilt_change_abs_deg"] = X["tilt_change_abs_deg"].abs()
    usable = X.notna().all(axis=1) & df["risk_state"].isin(CLASSES)
    X, d = X[usable], df[usable]
    if d.empty:
        raise SystemExit("No rows with all features (sensors uncalibrated/faulty?). Complete calibration first.")
    pred = bundle["model"].predict(X)
    result = {
        "DATA_STATUS": ("REAL DEVICE DATA" if sources == ["device"] else f"INCLUDES SIMULATED DATA {sources}"),
        "model_training_data": bundle.get("training_data"),
        "input_csv": Path(args.csv).name, "rows_used": int(len(d)), "rows_skipped_missing_features": int((~usable).sum()),
        "experiments": sorted(map(str, d["experiment_id"].dropna().unique())) if "experiment_id" in d else [],
        "agreement_with_rule_state": {**metrics_block(d["risk_state"].values, pred),
                                      "note": "Agreement between two classifiers; NOT validation."},
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    if args.label_column:
        lab = d[args.label_column]
        ok = lab.isin(CLASSES)
        result["against_independent_labels"] = {
            **metrics_block(lab[ok].values, pred[ok.values]), "label_column": args.label_column,
            "note": "Valid only if these labels were produced independently of the rule engine."}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "real_eval_metrics.json").write_text(json.dumps(result, indent=2))
    a = result["agreement_with_rule_state"]
    print(f"[{result['DATA_STATUS']}] rows={len(d)} agreement with rule state: accuracy={a['accuracy']:.3f} macroF1={a['macro_f1']:.3f}")
    print(f"written {out / 'real_eval_metrics.json'}")


if __name__ == "__main__":
    main()
