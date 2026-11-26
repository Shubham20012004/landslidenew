"""Train the experimental Random Forest on the SYNTHETIC dataset.

    python ml/train_model.py                      # full run
    python ml/train_model.py --quick --out tmpdir # small grid (smoke test)

Everything produced here is trained on SYNTHETIC data and is labelled so.
It demonstrates the software pipeline; it is not evidence that the physical
prototype or any classifier detects real landslide conditions.

Design decisions (see docs/ML_NOTES.md):
  * Inputs: columns that actually exist in the CSV. The report's rain duration
    feature (Table 8.1) is NOT in the CSV and is therefore not used.
  * Units: CSV moisture_index / rain_index are 0-100 -> divided by 100 to the
    report's [0,1] indices; moisture_rate_per_min is percentage-points/min ->
    divided by 100 to index/min. Tilt columns are already in degrees and deg/min.
    tilt_change_deg is used as |tilt change| to match the firmware's component.
  * Split: by experiment_id (StratifiedGroupKFold), so test experiments are
    never seen in training. A random-row split is reported ONLY as a leakage
    comparison.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import sklearn  # noqa: E402
from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score,  # noqa: E402
                             precision_score, recall_score)
from sklearn.model_selection import (GridSearchCV, GroupKFold,  # noqa: E402
                                     StratifiedGroupKFold, train_test_split)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from backend.risk_engine import (ORDERED_STATES, STATE_NAMES, RainDuration,  # noqa: E402
                                 RiskInputs, RiskParams, compute)

DEFAULT_CSV = REPO / "ml" / "data" / "landslide_synthetic_experimental_dataset_5000.csv"
CLASSES = ORDERED_STATES
FEATURES = ["moisture", "rain", "tilt_change_abs_deg", "moisture_rate_per_min", "tilt_rate_deg_per_min"]
REQUIRED = ["experiment_id", "timestamp", "moisture_index", "rain_index", "tilt_change_deg",
            "moisture_rate_per_min", "tilt_rate_deg_per_min", "risk_label"]
WATERMARK = "SYNTHETIC DATA — software demonstration only"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def profile(df: pd.DataFrame) -> dict:
    g = df.groupby("experiment_id")
    return {
        "rows": int(len(df)), "columns": list(df.columns),
        "dtypes": {c: str(t) for c, t in df.dtypes.items()},
        "missing": {c: int(v) for c, v in df.isna().sum().items()},
        "class_counts": {k: int(v) for k, v in df["risk_label"].value_counts().items()},
        "experiments": int(g.ngroups),
        "rows_per_experiment": {"min": int(g.size().min()), "max": int(g.size().max())},
        "experiments_containing_class": {c: int(g["risk_label"].apply(lambda s, c=c: (s == c).any()).sum()) for c in CLASSES},
        "numeric_ranges": {c: [float(df[c].min()), float(df[c].max())]
                           for c in df.columns if pd.api.types.is_numeric_dtype(df[c])},
    }


def to_features(df: pd.DataFrame) -> pd.DataFrame:
    """Convert CSV units to the report/firmware units (normalized indices, per minute)."""
    return pd.DataFrame({
        "moisture": df["moisture_index"] / 100.0,
        "rain": df["rain_index"] / 100.0,
        "tilt_change_abs_deg": df["tilt_change_deg"].abs(),
        "moisture_rate_per_min": df["moisture_rate_per_min"] / 100.0,
        "tilt_rate_deg_per_min": df["tilt_rate_deg_per_min"],
    })


def metrics_block(y_true, y_pred) -> dict:
    idx = {c: i for i, c in enumerate(CLASSES)}
    cm = confusion_matrix(y_true, y_pred, labels=CLASSES)
    rec = recall_score(y_true, y_pred, labels=CLASSES, average=None, zero_division=0)
    dist = np.abs(np.array([idx[a] for a in y_true]) - np.array([idx[b] for b in y_pred]))
    support = {c: int((np.asarray(y_true) == c).sum()) for c in CLASSES}
    return {
        "n": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_precision": float(precision_score(y_true, y_pred, labels=CLASSES, average="macro", zero_division=0)),
        "macro_recall": float(recall_score(y_true, y_pred, labels=CLASSES, average="macro", zero_division=0)),
        "macro_f1": float(f1_score(y_true, y_pred, labels=CLASSES, average="macro", zero_division=0)),
        "per_class_recall": {c: (float(r) if support[c] else None) for c, r in zip(CLASSES, rec)},
        "critical_recall": float(rec[3]) if support["CRITICAL"] else None,
        "support": support,
        "confusion_matrix": {"labels": CLASSES, "rows_true_cols_pred": cm.tolist()},
        "ordinal_error_distance": {
            "counts": {str(k): int((dist == k).sum()) for k in range(4)},
            "mean_abs_class_distance": float(dist.mean()),
            "errors_two_or_more_levels": int((dist >= 2).sum()),
        },
    }


def rule_engine_agreement(df: pd.DataFrame) -> dict:
    """Apply the PROVISIONAL rule engine (from the .ino) to the CSV for information only.

    Rain duration is not in the CSV; it is derived here per experiment with the
    CSV's 60 s step using the firmware's wet threshold. Hysteresis is not applied
    (row-wise raw state), and trends are used for every row.
    """
    p = RiskParams.from_ino()
    from backend.ino_config import load_ino_config

    th = load_ino_config()["RAIN_WET_THRESHOLD"]
    feats = to_features(df)
    preds = []
    for _, grp in df.groupby("experiment_id", sort=False):
        rd = RainDuration(th)
        for i in grp.index:
            f = feats.loc[i]
            d = rd.update(float(f["rain"]), 60.0)
            out = compute(RiskInputs(m=f["moisture"], dm=f["moisture_rate_per_min"], r=f["rain"], drS=d,
                                     tilt=f["tilt_change_abs_deg"], tiltRate=f["tilt_rate_deg_per_min"]), p)
            preds.append((i, STATE_NAMES[out.raw]))
    pred = pd.Series(dict(preds)).loc[df.index]
    m = metrics_block(df["risk_label"].values, pred.values)
    m["note"] = ("Informational: agreement between the CSV's labels and the PROVISIONAL firmware rule engine. "
                 "Not a validation of either.")
    return m


def plot_confusion(cm, title, path):
    cm = np.asarray(cm)
    fig, ax = plt.subplots(figsize=(5.6, 4.8))
    ax.imshow(cm, cmap="Blues")
    for i in range(4):
        for j in range(4):
            ax.text(j, i, str(cm[i, j]), ha="center", va="center",
                    color="white" if cm[i, j] > cm.max() / 2 else "black", fontsize=10)
    ax.set_xticks(range(4), CLASSES, rotation=20)
    ax.set_yticks(range(4), CLASSES)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title(title, fontsize=10)
    fig.text(0.5, 0.01, WATERMARK, ha="center", fontsize=8, color="crimson")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_bars(names, values, title, path, xlabel, errors=None):
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    order = np.argsort(values)
    ax.barh(np.array(names)[order], np.array(values)[order],
            xerr=None if errors is None else np.array(errors)[order], color="#4a78b5")
    ax.set_xlabel(xlabel)
    ax.set_title(title, fontsize=10)
    fig.text(0.5, 0.01, WATERMARK, ha="center", fontsize=8, color="crimson")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--out", default=str(REPO / "ml" / "outputs"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quick", action="store_true", help="tiny grid for smoke tests")
    args = ap.parse_args(argv)

    csv_path, out = Path(args.csv), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(csv_path)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise SystemExit(f"CSV is missing required columns {missing}; found {list(df.columns)}")
    bad = sorted(set(df["risk_label"]) - set(CLASSES))
    if bad:
        raise SystemExit(f"Unexpected labels {bad}")
    df = df.dropna(subset=REQUIRED).reset_index(drop=True)
    prof = profile(df)
    print(f"[SYNTHETIC] {csv_path.name}: {prof['rows']} rows, {prof['experiments']} experiments, classes {prof['class_counts']}")

    X, y, groups = to_features(df), df["risk_label"].values, df["experiment_id"].values
    n_groups = len(np.unique(groups))
    if n_groups < 5:
        raise SystemExit(f"Only {n_groups} experiments: a group split is not possible; results would be exploratory only.")

    # ---- held-out test experiments (group split, stratified by label as far as groups allow) ----
    sgkf = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=args.seed)
    folds = list(sgkf.split(X, y, groups))
    test_fold = next((k for k, (_, te) in enumerate(folds) if set(y[te]) == set(CLASSES)), 0)
    tr, te = folds[test_fold]
    split_info = {
        "method": "StratifiedGroupKFold(n_splits=5) by experiment_id; one fold held out as test",
        "test_fold_index": test_fold, "train_rows": int(len(tr)), "test_rows": int(len(te)),
        "train_experiments": sorted(map(str, np.unique(groups[tr]))),
        "test_experiments": sorted(map(str, np.unique(groups[te]))),
        "overlap_experiments": sorted(set(groups[tr]) & set(groups[te])),
    }
    assert not split_info["overlap_experiments"]

    # ---- small documented hyperparameter search (inner GroupKFold on training experiments) ----
    grid = ({"n_estimators": [100], "max_depth": [8], "min_samples_leaf": [1], "max_features": ["sqrt"]} if args.quick
            else {"n_estimators": [100, 300], "max_depth": [None, 8, 16], "min_samples_leaf": [1, 5],
                  "max_features": ["sqrt", None]})
    base = RandomForestClassifier(class_weight="balanced", random_state=args.seed, n_jobs=-1)
    search = GridSearchCV(base, grid, scoring="f1_macro", cv=GroupKFold(n_splits=4 if not args.quick else 3),
                          n_jobs=1, refit=True)
    t0 = time.time()
    search.fit(X.iloc[tr], y[tr], groups=groups[tr])
    model = search.best_estimator_
    print(f"best params {search.best_params_} (inner grouped CV macro-F1 {search.best_score_:.3f}) in {time.time()-t0:.1f}s")

    pred = model.predict(X.iloc[te])
    test_metrics = metrics_block(y[te], pred)

    # ---- outer grouped CV with the chosen hyperparameters (robustness across experiment splits) ----
    cv_rows = []
    for k, (a, b) in enumerate(folds):
        m = RandomForestClassifier(class_weight="balanced", random_state=args.seed, n_jobs=-1, **search.best_params_)
        m.fit(X.iloc[a], y[a])
        mb = metrics_block(y[b], m.predict(X.iloc[b]))
        cv_rows.append({"fold": k, "macro_f1": mb["macro_f1"], "accuracy": mb["accuracy"],
                        "critical_recall": mb["critical_recall"], "critical_support": mb["support"]["CRITICAL"]})
    crit = [r["critical_recall"] for r in cv_rows if r["critical_recall"] is not None]
    grouped_cv = {
        "folds": cv_rows,
        "macro_f1_mean": float(np.mean([r["macro_f1"] for r in cv_rows])),
        "macro_f1_std": float(np.std([r["macro_f1"] for r in cv_rows])),
        "critical_recall_mean_over_folds_with_critical": float(np.mean(crit)) if crit else None,
        "folds_without_critical": int(sum(r["critical_recall"] is None for r in cv_rows)),
    }

    # ---- leakage comparison: random-row split (NOT independent validation) ----
    a, b = train_test_split(np.arange(len(df)), test_size=0.2, stratify=y, random_state=args.seed)
    leak = RandomForestClassifier(class_weight="balanced", random_state=args.seed, n_jobs=-1, **search.best_params_)
    leak.fit(X.iloc[a], y[a])
    leak_m = metrics_block(y[b], leak.predict(X.iloc[b]))
    leakage = {"WARNING": "Random-row split puts samples of the same experiment in train and test. "
                          "Shown ONLY to illustrate optimistic leakage; never cite as validation.",
               "accuracy": leak_m["accuracy"], "macro_f1": leak_m["macro_f1"], "critical_recall": leak_m["critical_recall"]}

    # ---- importances ----
    imp = dict(zip(FEATURES, map(float, model.feature_importances_)))
    perm = permutation_importance(model, X.iloc[te], y[te], scoring="f1_macro", n_repeats=10 if not args.quick else 2,
                                  random_state=args.seed, n_jobs=1)
    perm_imp = {f: {"mean": float(mu), "std": float(sd)} for f, mu, sd in zip(FEATURES, perm.importances_mean, perm.importances_std)}

    rule_cmp = rule_engine_agreement(df)

    # ---- figures ----
    tag = "[SYNTHETIC]"
    plot_confusion(test_metrics["confusion_matrix"]["rows_true_cols_pred"],
                   f"{tag} RF confusion matrix — held-out experiments", out / "confusion_matrix.png")
    plot_bars(FEATURES, [imp[f] for f in FEATURES], f"{tag} RF impurity feature importance", out / "feature_importance.png",
              "mean decrease in impurity")
    plot_bars(FEATURES, [perm_imp[f]["mean"] for f in FEATURES], f"{tag} Permutation importance (test, macro-F1)",
              out / "permutation_importance.png", "drop in macro-F1", errors=[perm_imp[f]["std"] for f in FEATURES])
    counts = [prof["class_counts"].get(c, 0) for c in CLASSES]
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    ax.bar(CLASSES, counts, color=["#2e9d4a", "#e0c200", "#f08a00", "#d32f2f"])
    ax.set_title(f"{tag} Class distribution ({prof['rows']} rows, {prof['experiments']} experiments)", fontsize=10)
    fig.text(0.5, 0.01, WATERMARK, ha="center", fontsize=8, color="crimson")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out / "class_distribution.png", dpi=150)
    plt.close(fig)
    od = test_metrics["ordinal_error_distance"]["counts"]
    fig, ax = plt.subplots(figsize=(5.0, 3.2))
    ax.bar([f"{k} level(s)" for k in od], list(od.values()), color="#6d6d9e")
    ax.set_title(f"{tag} Ordinal distance of predictions from true class", fontsize=10)
    fig.text(0.5, 0.01, WATERMARK, ha="center", fontsize=8, color="crimson")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out / "ordinal_errors.png", dpi=150)
    plt.close(fig)

    training_data = f"SYNTHETIC: {csv_path.name} (sha256 {sha256(csv_path)[:16]}...)"
    label_note = ("Origin of the CSV risk_label is not documented (manual / rule-derived / generator). "
                  "If labels come from rules, high scores only show the forest reproduces that labelling "
                  "(report Sec. 8.16), not independent hazard detection.")
    metrics = {
        "DATA_STATUS": "SYNTHETIC — trained and evaluated on a synthetic dataset; NOT real prototype data, NOT real-world validation",
        "dataset": {"file": csv_path.name, "sha256": sha256(csv_path), "profile": prof},
        "features": FEATURES,
        "unit_conversion": {"moisture": "moisture_index / 100", "rain": "rain_index / 100",
                            "tilt_change_abs_deg": "abs(tilt_change_deg)",
                            "moisture_rate_per_min": "moisture_rate_per_min / 100 (index per minute)",
                            "tilt_rate_deg_per_min": "unchanged (deg per minute)",
                            "not_available": "rain_duration (report Table 8.1) is not in the CSV and is not used"},
        "label_note": label_note,
        "split": split_info,
        "hyperparameter_search": {"grid": grid, "inner_cv": "GroupKFold by experiment_id on training experiments",
                                  "scoring": "f1_macro", "best_params": search.best_params_,
                                  "best_inner_cv_macro_f1": float(search.best_score_), "class_weight": "balanced",
                                  "random_state": args.seed},
        "test_metrics_grouped_holdout": test_metrics,
        "grouped_cv_5fold": grouped_cv,
        "leakage_comparison_random_row_split": leakage,
        "feature_importance_impurity": imp,
        "permutation_importance_test": perm_imp,
        "rule_engine_vs_csv_labels": rule_cmp,
        "environment": {"python": platform.python_version(), "sklearn": sklearn.__version__, "numpy": np.__version__,
                        "pandas": pd.__version__},
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    (out / "dataset_profile.json").write_text(json.dumps(prof, indent=2))
    joblib.dump({"model": model, "features": FEATURES, "classes": list(model.classes_), "training_data": training_data,
                 "label_note": label_note, "unit_conversion": metrics["unit_conversion"], "split": split_info["method"],
                 "best_params": search.best_params_, "trained_at": metrics["trained_at"],
                 "sklearn_version": sklearn.__version__}, out / "rf_model.joblib")

    t = test_metrics
    print("[SYNTHETIC] held-out experiments:", ", ".join(split_info["test_experiments"]))
    print(f"[SYNTHETIC] accuracy={t['accuracy']:.3f} macroP={t['macro_precision']:.3f} macroR={t['macro_recall']:.3f} "
          f"macroF1={t['macro_f1']:.3f} CRITICAL recall={t['critical_recall']}")
    print(f"[SYNTHETIC] per-class recall {t['per_class_recall']}")
    print(f"[SYNTHETIC] ordinal distance {t['ordinal_error_distance']}")
    print(f"[SYNTHETIC] 5-fold grouped CV macro-F1 {grouped_cv['macro_f1_mean']:.3f} +/- {grouped_cv['macro_f1_std']:.3f}")
    print(f"[LEAKAGE COMPARISON ONLY] random-row split macro-F1 {leakage['macro_f1']:.3f}")
    print(f"[INFO] provisional rule engine vs CSV labels: accuracy {rule_cmp['accuracy']:.3f}")
    print(f"outputs written to {out}")
    return metrics


if __name__ == "__main__":
    main()
