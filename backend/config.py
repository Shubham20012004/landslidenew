"""Backend configuration from environment variables (and the repository .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

try:  # .env in the repository root (optional)
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

APP_VERSION = "2.0.0"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    path = Path(raw) if raw else default
    return path if path.is_absolute() else (REPO_ROOT / path)


def parse_device_tokens(raw: str) -> dict[str, str]:
    """'esp32-node-1:tokenA,sim-node-1:tokenB' -> {device_id: token}."""
    tokens: dict[str, str] = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError("LANDSLIDE_DEVICE_TOKENS entries must look like device_id:token")
        device, token = item.split(":", 1)
        device, token = device.strip(), token.strip()
        if not device or len(token) < 16:
            raise ValueError(f"device token for {device!r} must be at least 16 characters")
        tokens[device] = token
    return tokens


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 8000
    db_path: Path = REPO_ROOT / "backend" / "data" / "landslide.db"
    backup_dir: Path = REPO_ROOT / "backend" / "data" / "backups"
    log_dir: Path = REPO_ROOT / "logs"
    artifacts_dir: Path = REPO_ROOT / "ml" / "artifacts"
    frontend_dir: Path = REPO_ROOT / "frontend"
    device_tokens: dict[str, str] = field(default_factory=dict)
    admin_token: str = ""
    demo_mode: bool = False
    allow_test_fixture_models: bool = False
    cors_origins: list[str] = field(default_factory=list)
    max_body_bytes: int = 65536
    device_offline_s: int = 10
    prediction_fresh_s: int = 10
    log_to_file: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            host=os.getenv("LANDSLIDE_HOST", "0.0.0.0"),
            port=_int("LANDSLIDE_PORT", 8000),
            db_path=_path("LANDSLIDE_DB", REPO_ROOT / "backend" / "data" / "landslide.db"),
            backup_dir=_path("LANDSLIDE_BACKUP_DIR", REPO_ROOT / "backend" / "data" / "backups"),
            log_dir=_path("LANDSLIDE_LOG_DIR", REPO_ROOT / "logs"),
            artifacts_dir=_path("LANDSLIDE_ARTIFACTS_DIR", REPO_ROOT / "ml" / "artifacts"),
            device_tokens=parse_device_tokens(os.getenv("LANDSLIDE_DEVICE_TOKENS", "")),
            admin_token=os.getenv("LANDSLIDE_ADMIN_TOKEN", "").strip(),
            demo_mode=_bool(os.getenv("LANDSLIDE_DEMO_MODE"), False),
            cors_origins=[o.strip() for o in os.getenv("LANDSLIDE_CORS_ORIGINS", "").split(",") if o.strip()],
            max_body_bytes=_int("LANDSLIDE_MAX_BODY_BYTES", 65536),
            device_offline_s=_int("LANDSLIDE_DEVICE_OFFLINE_S", 10),
            prediction_fresh_s=_int("LANDSLIDE_PREDICTION_FRESH_S", 10),
        )

    def problems(self) -> list[str]:
        """Configuration problems that prevent normal operation (reported at startup)."""
        issues = []
        if not self.device_tokens:
            issues.append("LANDSLIDE_DEVICE_TOKENS is empty: no device can upload telemetry (run setup to generate tokens)")
        if self.admin_token and len(self.admin_token) < 16:
            issues.append("LANDSLIDE_ADMIN_TOKEN must be at least 16 characters")
        if not self.admin_token:
            issues.append("LANDSLIDE_ADMIN_TOKEN is empty: dataset annotation and model reload are disabled")
        return issues
