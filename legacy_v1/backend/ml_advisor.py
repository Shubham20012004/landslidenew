"""Advisory Random Forest inference. The rule-based state stays authoritative.

The model file (ml/outputs/rf_model.joblib) is produced by ml/train_model.py
from the SYNTHETIC dataset. Its output is labelled advisory/synthetic everywhere.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("landslide.ml")

# Mapping from telemetry fields to the model's feature names (units already
# converted to the report's normalized form by ml/train_model.py).
FIELD_FOR_FEATURE = {
    "moisture": "moisture",
    "rain": "rain",
    "tilt_change_abs_deg": "tilt_deg",
    "moisture_rate_per_min": "moisture_rate_per_min",
    "tilt_rate_deg_per_min": "tilt_rate_deg_per_min",
}


class MLAdvisor:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.model = None
        self.features: list[str] = []
        self.meta: dict = {}
        self.error: str | None = None
        self.load()

    def load(self):
        p = Path(self.model_path)
        if not p.exists():
            self.error = f"model file not found: {p} (run ml/train_model.py)"
            log.warning(self.error)
            return
        try:
            import joblib

            bundle = joblib.load(p)
            self.model = bundle["model"]
            # single-sample inference: a thread pool per call costs far more than it saves
            if hasattr(self.model, "n_jobs"):
                self.model.n_jobs = 1
            self.features = list(bundle["features"])
            self.meta = {k: v for k, v in bundle.items() if k != "model"}
            self.error = None
        except Exception as e:  # pragma: no cover - defensive
            self.model, self.error = None, f"failed to load model: {e}"
            log.warning(self.error)

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def predict(self, reading: dict) -> tuple[str | None, float | None, str | None]:
        """Return (ml_state, confidence, note). Never raises."""
        if not self.loaded:
            return None, None, "ML_UNAVAILABLE"
        row = []
        for f in self.features:
            src = FIELD_FOR_FEATURE.get(f, f)
            v = reading.get(src)
            if v is None:
                return None, None, f"ML_SKIPPED: missing {src}"
            if f == "tilt_change_abs_deg":
                v = abs(v)
            row.append(float(v))
        try:
            import pandas as pd

            x = pd.DataFrame([row], columns=self.features, dtype=float)
            proba = self.model.predict_proba(x)[0]
            k = int(proba.argmax())
            return str(self.model.classes_[k]), float(proba[k]), "ADVISORY_SYNTHETIC_MODEL"
        except Exception as e:  # pragma: no cover
            return None, None, f"ML_ERROR: {e}"
