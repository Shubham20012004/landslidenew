"""Telemetry ingestion and reading/prediction queries."""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .. import repositories as repo
from ..logging_setup import kv
from ..schemas import TelemetryIn
from ..security import authenticate_device, client_host
from ..serializers import page_row_api, prediction_api, reading_api
from .common import check_id, check_risk, parse_time, services

router = APIRouter(prefix="/api", tags=["readings"])
log = logging.getLogger("landslide.api")


def _reject_constant(name: str):
    raise ValueError(f"invalid JSON number {name}")


@router.post("/readings", status_code=201)
async def post_reading(request: Request):
    """ESP32 telemetry upload (schema 2.x). Requires X-Device-Id and X-Device-Token."""
    s = services(request)
    try:
        device_id = authenticate_device(request, s.settings)
    except HTTPException:
        claimed = (request.headers.get("x-device-id") or "")[:64] or None
        await run_in_threadpool(s.ingestion.record_rejection, claimed, "AUTH_REJECTED",
                                "Telemetry rejected: device authentication failed", {"client": client_host(request)})
        raise
    body = await request.body()
    try:
        data = json.loads(body, parse_constant=_reject_constant)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="body is not valid JSON (NaN/Infinity are not allowed)")
    try:
        telemetry = TelemetryIn.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors(include_url=False, include_context=False, include_input=False)
        ][:20]
        log.warning("telemetry rejected %s", kv(device=device_id, errors=len(errors), first=errors[0]["loc"] if errors else ""))
        await run_in_threadpool(s.ingestion.record_rejection, device_id, "TELEMETRY_REJECTED",
                                f"Telemetry rejected: {len(errors)} validation error(s), first at {errors[0]['loc'] if errors else '?'}",
                                {"errors": errors[:5]})
        return JSONResponse(status_code=422, content={"status": "error", "error": {
            "code": 422, "message": "telemetry validation failed", "details": errors}})
    if telemetry.device_id != device_id:
        raise HTTPException(status_code=403, detail="device_id in the payload does not match the authenticated device")
    response, created = await run_in_threadpool(s.ingestion.ingest, telemetry, client_host(request))
    return JSONResponse(status_code=201 if created else 200, content=response)


@router.get("/readings/latest")
def latest_reading(request: Request, device_id: str | None = None):
    """Latest LIVE reading (buffered replays never replace it) with its prediction."""
    s = services(request)
    device_id = check_id(device_id, "device_id")
    now = time.time()
    with s.db.read() as conn:
        r = repo.latest_live_reading(conn, device_id)
        p = repo.get_prediction(conn, r["id"]) if r is not None else None
    if r is None:
        return {"reading": None, "server_ts": now}
    return {"reading": reading_api(r, p, s.settings.prediction_fresh_s, now), "server_ts": now}


@router.get("/predictions/latest")
def latest_prediction(request: Request, device_id: str | None = None):
    s = services(request)
    device_id = check_id(device_id, "device_id")
    now = time.time()
    with s.db.read() as conn:
        r = repo.latest_live_reading(conn, device_id)
        p = repo.get_prediction(conn, r["id"]) if r is not None else None
    if r is None:
        return {"prediction": None, "server_ts": now}
    return {
        "device_id": r["device_id"],
        "session_id": r["session_id"],
        "reading_id": r["reading_id"],
        "prediction": prediction_api(p, s.settings.prediction_fresh_s, now),
        "server_ts": now,
    }


def _filter(device_id, session_id, start, end, risk_state, origin) -> repo.ReadingFilter:
    if origin not in (None, "", "device", "simulated"):
        raise HTTPException(status_code=422, detail="origin must be device or simulated")
    flt = repo.ReadingFilter(
        device_id=check_id(device_id, "device_id"),
        session_id=check_id(session_id, "session_id"),
        start=parse_time(start, "start"),
        end=parse_time(end, "end"),
        risk_state=check_risk(risk_state),
        origin=origin or None,
    )
    if flt.start is not None and flt.end is not None and flt.end <= flt.start:
        raise HTTPException(status_code=422, detail="end must be after start")
    return flt


@router.get("/readings/history")
def reading_history(
    request: Request,
    device_id: str | None = None,
    session_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    risk_state: str | None = None,
    origin: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    before: int | None = Query(None, ge=1, description="keyset cursor: return readings with pk < before"),
    with_total: bool = False,
):
    """Paginated readings, newest first (keyset pagination via `before`)."""
    s = services(request)
    flt = _filter(device_id, session_id, start, end, risk_state, origin)
    with s.db.read() as conn:
        rows = repo.reading_page(conn, flt, limit + 1, before)
        total = repo.count_readings(conn, flt) if with_total else None
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [page_row_api(r) for r in rows],
        "next_before": rows[-1]["id"] if has_more and rows else None,
        "total": total,
    }


@router.get("/readings/series")
def reading_series(
    request: Request,
    device_id: str | None = None,
    session_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    origin: str | None = None,
    max_points: int = Query(600, ge=10, le=3000),
):
    """Chart series for soil moisture, water wetness, relative tilt and vibration rate."""
    s = services(request)
    flt = _filter(device_id, session_id, start, end, None, origin)
    with s.db.read() as conn:
        data = repo.reading_series(conn, flt, max_points)
    return data
