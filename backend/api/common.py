"""Helpers shared by the API routers."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, Request

from ..config import Settings
from ..database import Database
from ..services.ingestion import IngestionService
from ..services.model_service import ModelService
from ..services.realtime import Hub

RISK_FILTERS = {"SAFE", "WATCH", "WARNING", "CRITICAL", "UNAVAILABLE"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")


@dataclass
class Services:
    settings: Settings
    db: Database
    model: ModelService
    hub: Hub
    ingestion: IngestionService


def services(request: Request) -> Services:
    return request.app.state.services


def parse_time(value: str | None, name: str) -> float | None:
    """Accepts epoch seconds or ISO-8601 (naive values are treated as UTC)."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{name}: expected ISO-8601 time or epoch seconds") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def check_id(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    if not ID_RE.match(value):
        raise HTTPException(status_code=422, detail=f"invalid {name}")
    return value


def check_risk(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    value = value.upper()
    if value not in RISK_FILTERS:
        raise HTTPException(status_code=422, detail=f"risk_state must be one of {sorted(RISK_FILTERS)}")
    return value
