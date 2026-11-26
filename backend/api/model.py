"""Model status, genuine evaluation metrics, reload, and dataset annotation."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from ml.datasets import dataset_summary
from ml.features import SCHEMA
from ml.registry import RegistryError

from .. import repositories as repo
from ..database import iso
from ..schemas import AnnotationIn
from ..security import require_admin
from .common import check_id, services

router = APIRouter(prefix="/api", tags=["model", "dataset"])


@router.get("/model/status")
def model_status(request: Request):
    s = services(request)
    status = s.model.status()
    status["feature_schema_detail"] = SCHEMA
    index = s.model.registry.read_index()
    status["registry"] = {"active": index.get("active"), "previous": index.get("previous"),
                          "available": s.model.registry.list_versions()}
    return status


@router.get("/model/metrics")
def model_metrics(request: Request, version: str | None = None):
    """Evaluation results stored with the model artifact (never computed or invented here)."""
    s = services(request)
    version = version or s.model.active_version
    if not version:
        return {"version": None, "metrics": None, "reason": "no active model"}
    try:
        metrics = s.model.registry.read_metrics(version)
        meta = s.model.registry.read_metadata(version)
    except RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    with s.db.read() as conn:
        runs = [dict(r) for r in repo.list_training_runs(conn)]
    return {"version": version, "provenance": meta.get("provenance"), "metrics": metrics, "training_runs": runs}


@router.post("/model/reload")
async def model_reload(request: Request):
    """Reload the registry's active model (after `python -m ml.train --activate` or a rollback)."""
    s = services(request)
    require_admin(request, s.settings)
    before = s.model.active_version
    ready = await run_in_threadpool(s.model.load)
    status = s.model.status()
    s.hub.publish("model", status)
    now = time.time()
    with s.db.transaction() as conn:
        repo.insert_event(conn, now=now, kind="MODEL_RELOADED" if ready else "MODEL_UNAVAILABLE",
                          severity="info" if ready else "warning",
                          message=(f"Model {status['active_version']} loaded" if ready
                                   else f"Model not ready: {status['reason']}"),
                          detail={"previous": before, "active": status["active_version"]})
    return status


# ------------------------------------------------------------------ dataset
@router.get("/dataset/summary")
def dataset_summary_route(request: Request):
    s = services(request)
    with s.db.read() as conn:
        return dataset_summary(conn)


def _annotation_api(a) -> dict:
    d = dict(a)
    d["start"] = iso(d.pop("start_time"))
    d["end"] = iso(d.pop("end_time"))
    d["created_at"] = iso(d["created_at"])
    return d


@router.get("/dataset/labels")
def list_labels(request: Request, device_id: str | None = None):
    s = services(request)
    with s.db.read() as conn:
        rows = repo.list_annotations(conn, check_id(device_id, "device_id"))
    return {"items": [_annotation_api(a) for a in rows]}


@router.post("/dataset/labels", status_code=201)
def create_label(request: Request, body: AnnotationIn):
    """Label a real experimental time range with the condition documented during the experiment.

    Labels are experiment observations; model predictions are never used as labels."""
    s = services(request)
    require_admin(request, s.settings)
    start, end = body.start.timestamp(), body.end.timestamp()
    now = time.time()
    with s.db.transaction() as conn:
        if repo.get_device(conn, body.device_id) is None:
            raise HTTPException(status_code=404, detail="unknown device")
        if body.session_id:
            sess = repo.get_session(conn, body.session_id)
            if sess is None or sess["device_id"] != body.device_id:
                raise HTTPException(status_code=404, detail="session not found for this device")
        n = repo.count_in_range(conn, body.device_id, body.session_id, start, end)
        if n == 0:
            raise HTTPException(status_code=422, detail="no stored readings in this time range")
        aid = repo.insert_annotation(conn, {"created_at": now, "device_id": body.device_id,
                                            "session_id": body.session_id, "start_time": start, "end_time": end,
                                            "label": body.label, "notes": body.notes, "author": body.author})
        repo.insert_event(conn, now=now, kind="ANNOTATION_ADDED", severity="info", device_id=body.device_id,
                          session_id=body.session_id,
                          message=f"Experiment label {body.label} added for {n} readings", detail={"annotation_id": aid})
        row = conn.execute("SELECT * FROM annotations WHERE id=?", (aid,)).fetchone()
    result = _annotation_api(row)
    result["readings_in_range"] = n
    return result


@router.delete("/dataset/labels/{annotation_id}")
def delete_label(request: Request, annotation_id: int):
    s = services(request)
    require_admin(request, s.settings)
    with s.db.transaction() as conn:
        if not repo.delete_annotation(conn, annotation_id):
            raise HTTPException(status_code=404, detail="annotation not found")
        repo.insert_event(conn, now=time.time(), kind="ANNOTATION_DELETED", severity="info",
                          message=f"Experiment label #{annotation_id} deleted")
    return {"deleted": annotation_id}
