"""Backend settings from environment variables / .env (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

try:  # optional: load .env from the repo root
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


def _bool(v: str | None) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    host: str = os.getenv("LANDSLIDE_HOST", "0.0.0.0")
    port: int = int(os.getenv("LANDSLIDE_PORT", "8000"))
    db_path: str = os.getenv("LANDSLIDE_DB", str(REPO_ROOT / "backend" / "data" / "landslide.db"))
    model_path: str = os.getenv("LANDSLIDE_MODEL", str(REPO_ROOT / "ml" / "outputs" / "rf_model.joblib"))
    dashboard_dir: str = str(REPO_ROOT / "dashboard")
    historical_summary: str = str(REPO_ROOT / "historical" / "outputs" / "summary.json")
    ml_metrics: str = str(REPO_ROOT / "ml" / "outputs" / "metrics.json")
    telegram_enabled: bool = _bool(os.getenv("TELEGRAM_ENABLED", "false"))
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")


def get_settings() -> Settings:
    return Settings()
