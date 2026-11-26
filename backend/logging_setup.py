"""Structured logging to the console and a rotating file."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(log_dir: Path | None, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, "_landslide", False):
            root.removeHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT, "%H:%M:%S"))
    console._landslide = True  # type: ignore[attr-defined]
    root.addHandler(console)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "backend.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(logging.Formatter(FORMAT))
        fh._landslide = True  # type: ignore[attr-defined]
        root.addHandler(fh)
    # Access logs for a 2-second telemetry stream are noise; errors are logged by the app.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def kv(**fields) -> str:
    """key=value formatting for structured log lines (values are repr-quoted if needed)."""
    parts = []
    for key, value in fields.items():
        text = str(value)
        if not text or any(c in text for c in ' ="'):
            text = '"' + text.replace('"', "'") + '"'
        parts.append(f"{key}={text}")
    return " ".join(parts)
