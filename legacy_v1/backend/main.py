"""FastAPI backend: ingestion, storage, advisory ML, dashboard API.

Run with:  python -m backend.run      (binds 0.0.0.0:8000 and prints the LAN IP)
"""
from __future__ import annotations

import csv
import io
import json
import logging
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional, Union

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .db import READING_COLUMNS, Database, iso
from .ino_config import load_ino_config
from .ml_advisor import MLAdvisor
from .models import ExperimentStart, Reading
from .notify import Notifier
from .settings import Settings, get_settings

log = logging.getLogger("landslide")

STATE_ORDER = {"SAFE": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}


def lan_ip() -> str:
    """Best-effort LAN IP (UDP connect sends no packets)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Ingestor:
    """Stores readings, adds advisory ML output and derives events."""

    def __init__(self, db: Database, ml: MLAdvisor, notifier: Notifier):
        self.db, self.ml, self.notifier = db, ml, notifier
        self.stream_state: dict[tuple, dict] = {}

    def _prev(self, device_id: str, source: str) -> dict:
        key = (device_id, source)
        if key not in self.stream_state:
            last = self.db.last_for_stream(device_id, source)
            self.stream_state[key] = {
                "state": last["risk_state"] if last else None,
                "faults": set(last["faults"]) if last else set(),
                "disagree": bool(last["ml_disagree"]) if last and last["ml_disagree"] is not None else False,
            }
        return self.stream_state[key]

    def ingest(self, r: Reading) -> dict:
        now = time.time()
        row = r.model_dump()
        row["server_time"] = now
        row["server_time_iso"] = iso(now)
        row["latency_ms"] = (now * 1000.0 - r.device_time_ms) if r.device_time_ms else None
        exp = self.db.active_experiment()
        row["experiment_id"] = exp["id"] if exp else None
        if exp and not row.get("scenario"):
            row["scenario"] = exp["scenario"]

        ml_state, ml_conf, note = self.ml.predict(row)
        row["ml_state"], row["ml_confidence"], row["ml_note"] = ml_state, ml_conf, note
        disagree = None
        if ml_state is not None and r.risk_state in STATE_ORDER:
            disagree = ml_state != r.risk_state
        row["ml_disagree"] = disagree
        prev = self._prev(r.device_id, r.source)  # must be read BEFORE inserting this reading
        rid = self.db.insert_reading(row)

        base = dict(device_id=r.device_id, source=r.source, experiment_id=row["experiment_id"], reading_id=rid,
                    server_time=now)
        if prev["state"] != r.risk_state:
            self.db.insert_event(kind="STATE_CHANGE", from_state=prev["state"], to_state=r.risk_state,
                                 risk_score=r.risk_score, detail=f"health={r.health}", **base)
            self.notifier.state_change(r.device_id, r.source, prev["state"], r.risk_state, r.risk_score)
            prev["state"] = r.risk_state
        faults = set(r.faults)
        for f in sorted(faults - prev["faults"]):
            self.db.insert_event(kind="FAULT_ON", detail=f, risk_score=r.risk_score, **base)
        for f in sorted(prev["faults"] - faults):
            self.db.insert_event(kind="FAULT_OFF", detail=f, risk_score=r.risk_score, **base)
        prev["faults"] = faults
        if disagree and not prev["disagree"]:
            self.db.insert_event(kind="ML_DISAGREE", from_state=r.risk_state, to_state=ml_state,
                                 risk_score=r.risk_score, detail="advisory ML (synthetic-trained) differs from rule state",
                                 **base)
        prev["disagree"] = bool(disagree)
        return {"id": rid, "rule_state": r.risk_state, "ml_state": ml_state, "ml_confidence": ml_conf,
                "disagree": disagree, "experiment_id": row["experiment_id"]}


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.db_path)
    ml = MLAdvisor(settings.model_path)
    notifier = Notifier(settings.telegram_enabled, settings.telegram_token, settings.telegram_chat_id)
    ingestor = Ingestor(db, ml, notifier)
    started = time.time()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        db.close()

    app = FastAPI(title="Landslide Risk Monitor (prototype)", version="1.0.0", lifespan=lifespan,
                  description="Prototype risk states only - not a validated landslide prediction system.")
    app.state.db, app.state.ml, app.state.settings = db, ml, settings

    @app.post("/api/readings")
    def post_readings(body: Union[List[Reading], Reading] = Body(...)):
        items = body if isinstance(body, list) else [body]
        if not items:
            raise HTTPException(400, "empty list")
        if len(items) > 200:
            raise HTTPException(413, "max 200 readings per request")
        results = [ingestor.ingest(r) for r in items]
        last = results[-1]
        return {"stored": len(results), "ids": [x["id"] for x in results], "rule_state": last["rule_state"],
                "ml_state": last["ml_state"], "disagree": last["disagree"]}

    @app.get("/api/latest")
    def get_latest(source: str = Query("any", pattern="^(any|device|simulated)$")):
        r = db.latest(None if source == "any" else source)
        if not r:
            return {"reading": None, "message": "no readings yet"}
        age = time.time() - r["server_time"]
        return {"reading": r, "age_s": round(age, 1), "stale": age > 30,
                "authoritative_state": r["risk_state"], "ml_advisory_state": r["ml_state"],
                "disagree": r["ml_disagree"], "active_experiment": db.active_experiment()}

    @app.get("/api/history")
    def get_history(minutes: float = Query(10, gt=0, le=60 * 24 * 7),
                    source: str = Query("any", pattern="^(any|device|simulated)$"),
                    limit: int = Query(5000, ge=1, le=50000)):
        rows = db.history(minutes, None if source == "any" else source, limit)
        return {"minutes": minutes, "count": len(rows), "readings": rows}

    @app.get("/api/events")
    def get_events(limit: int = Query(100, ge=1, le=2000),
                   source: str = Query("any", pattern="^(any|device|simulated)$")):
        return {"events": db.events(limit, None if source == "any" else source)}

    @app.get("/api/export.csv")
    def export_csv(source: str = Query("any", pattern="^(any|device|simulated)$"),
                   experiment_id: Optional[int] = None):
        rows = db.all_readings(None if source == "any" else source, experiment_id)
        cols = ["id"] + [c for c, _ in READING_COLUMNS]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([json.dumps(r[c]) if c == "faults" else ("" if r[c] is None else r[c]) for c in cols])
        name = f"landslide_readings_{source}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/experiments/start")
    def experiment_start(body: ExperimentStart):
        exp = db.start_experiment(body.name, body.scenario, body.notes)
        db.insert_event(kind="EXPERIMENT_START", detail=f"{exp['name']} ({exp['scenario']})", experiment_id=exp["id"])
        return {"experiment": exp}

    @app.post("/api/experiments/stop")
    def experiment_stop():
        exp = db.stop_experiment()
        if not exp:
            raise HTTPException(409, "no active experiment")
        db.insert_event(kind="EXPERIMENT_STOP", detail=exp["name"], experiment_id=exp["id"])
        return {"experiment": exp}

    @app.get("/api/experiments")
    def experiments():
        return {"experiments": db.experiments(), "active": db.active_experiment()}

    @app.get("/api/health")
    def health():
        dev = db.latest("device")
        return {
            "status": "ok", "server_time": time.time(), "uptime_s": round(time.time() - started, 1),
            "lan_ip": lan_ip(), "port": settings.port,
            "esp32_post_url": f"http://{lan_ip()}:{settings.port}/api/readings",
            "db_path": settings.db_path, "readings_device": db.count("device"), "readings_simulated": db.count("simulated"),
            "last_device_reading_age_s": round(time.time() - dev["server_time"], 1) if dev else None,
            "ml_model_loaded": ml.loaded, "ml_error": ml.error,
            "ml_training_data": ml.meta.get("training_data") if ml.loaded else None,
            "telegram_enabled": notifier.enabled,
        }

    @app.get("/api/config")
    def config():
        c = load_ino_config()
        keys = ["TAU1", "TAU2", "TAU3", "HYSTERESIS", "MIN_DWELL_MS", "W_MOISTURE", "W_RAIN", "W_TILT",
                "BETA_M", "BETA_R", "BETA_T", "RAIN_WET_THRESHOLD", "SAMPLE_INTERVAL_MS", "TELEMETRY_INTERVAL_MS",
                "MA_WINDOW_N", "RATE_WINDOW_L"]
        return {"source": "firmware/landslide_monitor/landslide_monitor.ino CONFIG region",
                "values": {k: c[k] for k in keys},
                "provisional": {k: "PROVISIONAL" in c["_comments"].get(k, "") for k in keys}}

    @app.get("/api/ml/info")
    def ml_info():
        p = Path(settings.ml_metrics)
        metrics = json.loads(p.read_text()) if p.exists() else None
        return {"loaded": ml.loaded, "error": ml.error, "features": ml.features,
                "meta": {k: v for k, v in ml.meta.items() if k in ("training_data", "label_note", "trained_at", "split")},
                "metrics": metrics}

    @app.get("/api/historical/summary")
    def historical_summary():
        p = Path(settings.historical_summary)
        if not p.exists():
            return JSONResponse({"available": False,
                                 "message": "Run historical/analyze_glc.py with the NASA Global Landslide Catalog CSV."})
        return {"available": True, "summary": json.loads(p.read_text())}

    dash = Path(settings.dashboard_dir)
    if dash.exists():
        app.mount("/static", StaticFiles(directory=str(dash)), name="static")

        @app.get("/", include_in_schema=False)
        def index():
            return FileResponse(dash / "index.html")

        for route, folder in (("/historical-figures", Path(settings.historical_summary).parent),
                              ("/ml-figures", Path(settings.ml_metrics).parent)):
            folder.mkdir(parents=True, exist_ok=True)
            app.mount(route, StaticFiles(directory=str(folder)), name=route.strip("/"))

    return app
