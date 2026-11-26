"""Active Random Forest model: loading, provenance policy and inference."""
from __future__ import annotations

import logging
import threading
import time
from typing import Mapping

from ml.features import SCHEMA_VERSION
from ml.predict import ModelBundle, Prediction
from ml.registry import Registry, RegistryError

from ..config import Settings
from ..logging_setup import kv

log = logging.getLogger("landslide.model")

PROVENANCE_LABEL = {
    "experimental": "real experimental data",
    "synthetic_demo": "SYNTHETIC demonstration data",
    "test_fixture": "automated test fixture",
}


class ModelService:
    """Loads the registry's active model once and serves predictions from memory."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.registry = Registry(settings.artifacts_dir)
        self._lock = threading.Lock()
        self.bundle: ModelBundle | None = None
        self.active_version: str | None = None
        self.active_metadata: dict | None = None
        self.reason = "model not loaded yet"
        self.loaded_at: float | None = None

    def allowed_provenances(self) -> set[str]:
        allowed = {"experimental"}
        if self.settings.demo_mode:
            allowed.add("synthetic_demo")
        if self.settings.allow_test_fixture_models:
            allowed.add("test_fixture")
        return allowed

    @property
    def ready(self) -> bool:
        return self.bundle is not None

    def load(self) -> bool:
        """(Re)load the active model. A failed load never leaves a half-loaded model."""
        with self._lock:
            self.bundle, self.active_metadata, self.loaded_at = None, None, None
            try:
                active = self.registry.read_index().get("active")
            except (OSError, ValueError) as exc:
                self.reason = f"model registry unreadable: {exc}"
                log.error(self.reason)
                return False
            self.active_version = active
            if not active:
                self.reason = "no active model in the registry (train one with train.bat / train.sh)"
                log.warning(self.reason)
                return False
            try:
                meta = self.registry.verify(active)
            except (RegistryError, OSError, ValueError) as exc:
                self.reason = f"active model failed verification: {exc}"
                log.error(self.reason)
                return False
            self.active_metadata = meta
            provenance = meta.get("provenance")
            if provenance not in self.allowed_provenances():
                if provenance == "synthetic_demo":
                    self.reason = (
                        "active model was trained on SYNTHETIC data; it is only used when demonstration mode is "
                        "enabled (LANDSLIDE_DEMO_MODE=true or start.bat demo)"
                    )
                else:
                    self.reason = f"model provenance '{provenance}' is not allowed in this configuration"
                log.warning(self.reason)
                return False
            try:
                bundle = ModelBundle.load(self.registry, active)
            except Exception as exc:  # noqa: BLE001 - any load failure means "not ready"
                self.reason = f"active model could not be loaded: {exc}"
                log.error(self.reason)
                return False
            self.bundle, self.loaded_at, self.reason = bundle, time.time(), ""
            log.info("model ready %s", kv(version=active, provenance=provenance, demo_mode=self.settings.demo_mode))
            return True

    def predict(self, features: Mapping[str, float | None]) -> Prediction:
        bundle = self.bundle
        if bundle is None:
            raise RuntimeError(self.reason or "model not ready")
        return bundle.predict(features)

    @property
    def provenance(self) -> str | None:
        return self.bundle.provenance if self.bundle else None

    def status(self) -> dict:
        meta = self.active_metadata or {}
        return {
            "ready": self.ready,
            "status": "READY" if self.ready else "MODEL_NOT_READY",
            "reason": self.reason or None,
            "active_version": self.active_version,
            "provenance": meta.get("provenance"),
            "provenance_label": PROVENANCE_LABEL.get(meta.get("provenance", ""), None),
            "demo_model": meta.get("provenance") == "synthetic_demo",
            "demo_mode": self.settings.demo_mode,
            "loaded_at": self.loaded_at,
            "feature_schema": SCHEMA_VERSION,
            "model_feature_schema": meta.get("feature_schema_version"),
            "created_at": meta.get("created_at"),
            "dataset": meta.get("dataset"),
            "features": meta.get("features"),
            "classes": meta.get("classes"),
            "hyperparameters": meta.get("hyperparameters"),
            "limitations": meta.get("limitations"),
        }
