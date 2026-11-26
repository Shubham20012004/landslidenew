"""Load a verified model artifact and run Random Forest inference."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

import joblib
import pandas as pd

from .features import CLASSES, FEATURE_NAMES, SCHEMA_VERSION
from .registry import Registry, RegistryError


@dataclass
class Prediction:
    risk_state: str
    confidence: float
    probabilities: dict[str, float]


class ModelBundle:
    """A loaded, integrity-checked model with its metadata."""

    def __init__(self, version: str, pipeline, metadata: dict):
        self.version = version
        self.pipeline = pipeline
        self.metadata = metadata
        self.features: list[str] = list(metadata["features"])
        self.classes: list[str] = [str(c) for c in pipeline.classes_]

    @property
    def provenance(self) -> str:
        return self.metadata["provenance"]

    @classmethod
    def load(cls, registry: Registry, version: str) -> "ModelBundle":
        meta = registry.verify(version)
        if meta["feature_schema_version"] != SCHEMA_VERSION:
            raise RegistryError(
                f"model {version} uses feature schema {meta['feature_schema_version']}, backend expects {SCHEMA_VERSION}"
            )
        if list(meta["features"]) != FEATURE_NAMES:
            raise RegistryError(f"model {version}: feature list/order differs from feature_schema.json")
        pipeline = joblib.load(registry.artifact_dir(version) / "model.joblib")
        # Single-row inference: thread fan-out costs more than it saves.
        if hasattr(pipeline, "named_steps") and "rf" in pipeline.named_steps:
            pipeline.named_steps["rf"].set_params(n_jobs=1)
        classes = [str(c) for c in getattr(pipeline, "classes_", [])]
        if not classes or any(c not in CLASSES for c in classes):
            raise RegistryError(f"model {version}: unexpected classes {classes}")
        return cls(version, pipeline, meta)

    def predict(self, features: Mapping[str, float | None]) -> Prediction:
        missing = [n for n in self.features if features.get(n) is None]
        if missing:
            raise ValueError(f"missing features: {', '.join(missing)}")
        row = pd.DataFrame([[float(features[n]) for n in self.features]], columns=self.features)
        proba = self.pipeline.predict_proba(row)[0]
        probs = {c: 0.0 for c in CLASSES}
        for cls_name, p in zip(self.classes, proba):
            probs[cls_name] = float(p)
        if not all(math.isfinite(p) for p in probs.values()):
            raise ValueError("model returned non-finite probabilities")
        best = max(self.classes, key=lambda c: probs[c])
        return Prediction(risk_state=best, confidence=probs[best], probabilities=probs)
