"""Health, devices, sessions, events, CSV export and the live event stream."""
from __future__ import annotations

import asyncio
import csv
import io
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from ml.datasets import labeled_readings_sql
from ml.features import FEATURE_NAMES

from .. import repositories as repo
from ..config import APP_VERSION
from ..database import iso
from ..serializers import device_api, event_api, reading_api, session_api
from ..services.realtime import sse_format
from .common import check_id, parse_time, services
from .readings import _filter

router = APIRouter(prefix="/api", tags=["system"])

SEVERITIES = {"info", "warning", "critical"}
COMM_KINDS = ["DEVICE_OFFLINE", "DEVICE_ONLINE", "AUTH_REJECTED", "TELEMETRY_REJECTED"]


@router.get("/health")
def health(request: Request):
    s = services(request)
    now = time.time()
    db_ok = s.db.ping()
    schema = s.db.schema_version() if db_ok else None
    model = s.model.status()
    problems = s.settings.problems()
    return {
        "status": "ok" if db_ok else "error",
        "version": APP_VERSION,
        "server_time": iso(now),
        "server_ts": now,
        "database": {"ok": db_ok, "schema_version": schema},
        "model": {k: model[k] for k in ("ready", "status", "reason", "active_version", "provenance", "demo_model",
                                        "demo_mode", "feature_schema")},
        "config": {
            "device_offline_s": s.settings.device_offline_s,
            "prediction_fresh_s": s.settings.prediction_fresh_s,
            "admin_enabled": bool(s.settings.admin_token),
            "registered_devices": sorted(s.settings.device_tokens),
        },
        "warnings": problems,
        "stream_subscribers": s.hub.subscriber_count,
    }


@router.get("/devices")
def devices(request: Request):
    s = services(request)
    now = time.time()
    with s.db.read() as conn:
        rows = repo.list_devices(conn)
    return {"items": [device_api(d, now, s.settings.device_offline_s) for d in rows], "server_ts": now}


@router.get("/sessions")
def sessions(request: Request, device_id: str | None = None, limit: int = Query(50, ge=1, le=500),
             offset: int = Query(0, ge=0)):
    s = services(request)
    with s.db.read() as conn:
        rows = repo.list_sessions(conn, check_id(device_id, "device_id"), limit, offset)
    return {"items": [session_api(r) for r in rows]}


@router.get("/sessions/{session_id}")
def session_detail(request: Request, session_id: str):
    s = services(request)
    check_id(session_id, "session_id")
    with s.db.read() as conn:
        row = repo.get_session(conn, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="session not found")
        summary = repo.session_risk_summary(conn, session_id)
        annotations = conn.execute(
            "SELECT * FROM annotations WHERE session_id=? OR (session_id IS NULL AND device_id=? AND end_time>=? AND start_time<=?)"
            " ORDER BY start_time", (session_id, row["device_id"], row["first_seen"], row["last_seen"])
        ).fetchall()
    data = session_api(row)
    data["prediction_summary"] = summary
    data["annotations"] = [dict(a) for a in annotations]
    return data


@router.get("/events")
def events(
    request: Request,
    device_id: str | None = None,
    session_id: str | None = None,
    kind: str | None = Query(None, description="comma-separated event kinds"),
    severity: str | None = Query(None, description="comma-separated: info,warning,critical"),
    category: str | None = Query(None, description="'communication' selects connection and upload events"),
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    before: int | None = Query(None, ge=1),
):
    s = services(request)
    kinds = [k.strip().upper() for k in kind.split(",") if k.strip()] if kind else None
    if category == "communication":
        kinds = COMM_KINDS
    severities = [v.strip().lower() for v in severity.split(",") if v.strip()] if severity else None
    if severities and not set(severities) <= SEVERITIES:
        raise HTTPException(status_code=422, detail="severity must be info, warning or critical")
    with s.db.read() as conn:
        rows = repo.list_events(conn, device_id=check_id(device_id, "device_id"),
                                session_id=check_id(session_id, "session_id"), kinds=kinds, severities=severities,
                                start=parse_time(start, "start"), end=parse_time(end, "end"), limit=limit + 1,
                                before_id=before)
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"items": [event_api(e) for e in rows], "next_before": rows[-1]["id"] if has_more and rows else None}


# ------------------------------------------------------------------ export
EXPORT_READING_COLUMNS = ["pk"] + [c for c in repo.LIST_COLUMNS] + [
    f"pred_{c}" for c in repo.PREDICTION_COLUMNS if c != "reading_pk"]


def _csv_rows(header: list[str], rows_iter):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    yield buf.getvalue()
    for row in rows_iter:
        buf.seek(0)
        buf.truncate(0)
        writer.writerow(row)
        yield buf.getvalue()


@router.get("/export/csv")
def export_csv(
    request: Request,
    scope: str = Query("readings", pattern="^(readings|labeled)$"),
    device_id: str | None = None,
    session_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    origin: str | None = None,
):
    """CSV export. scope=readings: stored readings with predictions (filters apply).
    scope=labeled: real device readings inside exactly one experiment annotation (training data)."""
    s = services(request)
    flt = _filter(device_id, session_id, start, end, None, origin)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())

    if scope == "readings":
        header = EXPORT_READING_COLUMNS

        def rows():
            with s.db.read() as conn:
                for r in repo.iter_export_rows(conn, flt):
                    d = dict(r)
                    d["pk"] = d.pop("id")
                    d["received_at"] = iso(d["received_at"])
                    d["pred_created_at"] = iso(d["pred_created_at"])
                    yield [d.get(c) for c in header]

        name = f"readings-{flt.session_id or flt.device_id or 'all'}-{stamp}.csv"
    else:
        header = ["reading_pk", "reading_id", "device_id", "session_id", "received_at", *FEATURE_NAMES, "label",
                  "annotation_id"]
        extra, args = "", []
        if flt.device_id:
            extra += " AND r.device_id = ?"
            args.append(flt.device_id)
        if flt.session_id:
            extra += " AND r.session_id = ?"
            args.append(flt.session_id)
        if flt.start is not None:
            extra += " AND r.received_at >= ?"
            args.append(flt.start)
        if flt.end is not None:
            extra += " AND r.received_at < ?"
            args.append(flt.end)
        sql = labeled_readings_sql(extra)

        def rows():
            with s.db.read() as conn:
                cur = conn.execute(sql, args)
                while batch := cur.fetchmany(500):
                    for r in batch:
                        d = dict(r)
                        d["received_at"] = iso(d["received_at"])
                        yield [d.get(c) for c in header]

        name = f"labeled-training-data-{stamp}.csv"
    name = "".join(ch for ch in name if ch.isalnum() or ch in "-_.")
    return StreamingResponse(_csv_rows(header, rows()), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ stream
def _snapshot(s) -> dict:
    now = time.time()
    with s.db.read() as conn:
        devs = repo.list_devices(conn)
        latest = {}
        for d in devs:
            if d["last_reading_pk"]:
                r = repo.get_reading(conn, d["last_reading_pk"])
                p = repo.get_prediction(conn, d["last_reading_pk"])
                if r is not None:
                    latest[d["device_id"]] = reading_api(r, p, s.settings.prediction_fresh_s, now)
        evs = repo.list_events(conn, device_id=None, session_id=None, kinds=None, severities=None, start=None,
                               end=None, limit=30, before_id=None)
    return {
        "server_ts": now,
        "devices": [device_api(d, now, s.settings.device_offline_s) for d in devs],
        "latest": latest,
        "events": [event_api(e) for e in evs],
        "model": s.model.status(),
    }


@router.get("/stream")
async def stream(request: Request):
    """Server-Sent Events: `snapshot` on connect, then `reading`, `event`, `model` and `heartbeat`."""
    s = services(request)
    queue = s.hub.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            snapshot = await run_in_threadpool(_snapshot, s)
            yield sse_format("snapshot", snapshot, s.hub.last_id)
            while s.hub.is_subscribed(queue):
                if await request.is_disconnected():
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    yield sse_format("heartbeat", {"server_ts": time.time()})
                    continue
                yield message
        finally:
            s.hub.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
