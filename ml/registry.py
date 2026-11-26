"""Model artifact registry: versioned artifacts, integrity checks, activation and rollback.

Layout::

    ml/artifacts/
        registry.json              {"active": "<version>", "previous": "<version>", "history": [...]}
        <version>/model.joblib     sklearn Pipeline (column selection + RandomForestClassifier)
        <version>/metadata.json    provenance, feature schema, classes, sha256 of model.joblib
        <version>/metrics.json     genuine evaluation results
        <version>/*.png            report figures

Only directories inside the artifacts folder are ever loaded (joblib/pickle
must never be loaded from untrusted locations). Directories whose name starts
with "_" are archives and are ignored.

CLI::

    python -m ml.registry list
    python -m ml.registry activate <version>
    python -m ml.registry rollback
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACTS_DIR = REPO_ROOT / "ml" / "artifacts"
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
PROVENANCES = ("experimental", "synthetic_demo", "test_fixture")


class RegistryError(Exception):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Registry:
    def __init__(self, root: Path | str = DEFAULT_ARTIFACTS_DIR):
        self.root = Path(root).resolve()

    @property
    def index_path(self) -> Path:
        return self.root / "registry.json"

    def read_index(self) -> dict:
        if not self.index_path.exists():
            return {"active": None, "previous": None, "history": []}
        with self.index_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        data.setdefault("history", [])
        return data

    def artifact_dir(self, version: str) -> Path:
        if not version or not VERSION_RE.match(version) or version.startswith("_"):
            raise RegistryError(f"invalid model version {version!r}")
        path = (self.root / version).resolve()
        if path.parent != self.root:
            raise RegistryError("artifact path escapes the artifacts directory")
        return path

    def read_metadata(self, version: str) -> dict:
        path = self.artifact_dir(version) / "metadata.json"
        if not path.exists():
            raise RegistryError(f"model {version} has no metadata.json")
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def read_metrics(self, version: str) -> dict | None:
        path = self.artifact_dir(version) / "metrics.json"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def verify(self, version: str) -> dict:
        """Integrity check: metadata present, model hash matches, required fields."""
        meta = self.read_metadata(version)
        model_path = self.artifact_dir(version) / "model.joblib"
        if not model_path.exists():
            raise RegistryError(f"model {version}: model.joblib missing")
        digest = sha256_file(model_path)
        if digest != meta.get("artifact_sha256"):
            raise RegistryError(f"model {version}: model.joblib hash mismatch (artifact modified or corrupted)")
        for key in ("model_version", "feature_schema_version", "features", "classes", "provenance"):
            if key not in meta:
                raise RegistryError(f"model {version}: metadata missing '{key}'")
        if meta["model_version"] != version:
            raise RegistryError(f"model {version}: metadata names a different version")
        if meta["provenance"] not in PROVENANCES:
            raise RegistryError(f"model {version}: unknown provenance {meta['provenance']!r}")
        return meta

    def list_versions(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            p.name for p in self.root.iterdir() if p.is_dir() and not p.name.startswith("_") and (p / "metadata.json").exists()
        )

    def activate(self, version: str, *, reason: str = "manual") -> dict:
        self.verify(version)
        index = self.read_index()
        if index.get("active") == version:
            return index
        index["previous"] = index.get("active")
        index["active"] = version
        index["history"].append({"version": version, "activated_at": time.time(), "reason": reason})
        index["history"] = index["history"][-50:]
        atomic_write_json(self.index_path, index)
        return index

    def rollback(self) -> dict:
        index = self.read_index()
        previous = index.get("previous")
        if not previous:
            raise RegistryError("no previous model to roll back to")
        self.verify(previous)
        return self.activate(previous, reason="rollback")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Model registry")
    parser.add_argument("--artifacts", default=str(DEFAULT_ARTIFACTS_DIR))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    act = sub.add_parser("activate")
    act.add_argument("version")
    sub.add_parser("rollback")
    args = parser.parse_args(argv)
    reg = Registry(args.artifacts)
    try:
        if args.cmd == "list":
            index = reg.read_index()
            for v in reg.list_versions():
                try:
                    meta = reg.verify(v)
                    status = "ok"
                except RegistryError as exc:
                    meta, status = {}, f"INVALID: {exc}"
                mark = "*" if v == index.get("active") else " "
                print(f"{mark} {v:40s} {meta.get('provenance', '?'):15s} {status}")
            if not reg.list_versions():
                print("no models in", reg.root)
        elif args.cmd == "activate":
            reg.activate(args.version)
            print("active model:", args.version, "(restart the backend or POST /api/model/reload)")
        elif args.cmd == "rollback":
            index = reg.rollback()
            print("active model:", index["active"])
    except RegistryError as exc:
        print("error:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
