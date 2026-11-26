"""Model evaluation: genuine metrics on held-out data and report figures."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, confusion_matrix,
                             precision_recall_fscore_support)

from .features import CLASSES, FEATURE_NAMES


def classification_metrics(y_true, y_pred) -> dict:
    labels = [c for c in CLASSES if c in set(y_true) | set(y_pred)]
    p, r, f, s = precision_recall_fscore_support(y_true, y_pred, labels=CLASSES, zero_division=0)
    macro = precision_recall_fscore_support(y_true, y_pred, labels=labels, average="macro", zero_division=0)
    weighted = precision_recall_fscore_support(y_true, y_pred, labels=labels, average="weighted", zero_division=0)
    return {
        "n": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "macro": {"precision": float(macro[0]), "recall": float(macro[1]), "f1": float(macro[2])},
        "weighted": {"precision": float(weighted[0]), "recall": float(weighted[1]), "f1": float(weighted[2])},
        "per_class": {
            c: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]), "support": int(s[i])}
            for i, c in enumerate(CLASSES)
        },
        "confusion_matrix": {
            "labels": CLASSES,
            "matrix": confusion_matrix(y_true, y_pred, labels=CLASSES).tolist(),
            "rows": "true class",
            "columns": "predicted class",
        },
    }


def majority_baseline(y_train, y_test) -> dict:
    majority = pd.Series(y_train).mode()[0]
    return {"predicts": str(majority), **{k: v for k, v in classification_metrics(
        y_test, np.full(len(y_test), majority)).items() if k in ("accuracy", "balanced_accuracy", "macro")}}


def feature_importances(pipeline, X_test: pd.DataFrame, y_test, seed: int) -> dict:
    rf = pipeline.named_steps["rf"]
    impurity = dict(zip(FEATURE_NAMES, map(float, rf.feature_importances_)))
    perm = permutation_importance(pipeline, X_test, y_test, scoring="balanced_accuracy", n_repeats=5,
                                  random_state=seed, n_jobs=-1)
    return {
        "impurity": dict(sorted(impurity.items(), key=lambda kv: -kv[1])),
        "permutation_balanced_accuracy": {
            name: {"mean": float(m), "std": float(sd)}
            for name, m, sd in sorted(zip(FEATURE_NAMES, perm.importances_mean, perm.importances_std),
                                      key=lambda x: -x[1])
        },
    }


def save_figures(metrics: dict, out_dir: Path, title_suffix: str) -> list[str]:
    """Confusion matrix and feature importance PNGs for the report."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    files = []
    cm = np.array(metrics["test"]["confusion_matrix"]["matrix"])
    fig, ax = plt.subplots(figsize=(5.2, 4.4), dpi=150)
    norm = cm / np.maximum(cm.sum(axis=1, keepdims=True), 1)
    ax.imshow(norm, cmap="Blues", vmin=0, vmax=1)
    ax.set_xticks(range(len(CLASSES)), CLASSES, rotation=30)
    ax.set_yticks(range(len(CLASSES)), CLASSES)
    ax.set_xlabel("Predicted class")
    ax.set_ylabel("True class")
    for i in range(len(CLASSES)):
        for j in range(len(CLASSES)):
            ax.text(j, i, f"{cm[i, j]}\n{norm[i, j]:.0%}", ha="center", va="center", fontsize=8,
                    color="white" if norm[i, j] > 0.6 else "black")
    ax.set_title(f"Held-out confusion matrix\n{title_suffix}", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_dir / "confusion_matrix.png")
    plt.close(fig)
    files.append("confusion_matrix.png")

    imp = metrics["feature_importance"]["permutation_balanced_accuracy"]
    names = list(imp)[::-1]
    fig, ax = plt.subplots(figsize=(6, 4.6), dpi=150)
    ax.barh(names, [imp[n]["mean"] for n in names], xerr=[imp[n]["std"] for n in names], color="#4a6fa5")
    ax.set_xlabel("Decrease in balanced accuracy when permuted")
    ax.set_title(f"Permutation feature importance (held-out)\n{title_suffix}", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_dir / "feature_importance.png")
    plt.close(fig)
    files.append("feature_importance.png")
    return files
