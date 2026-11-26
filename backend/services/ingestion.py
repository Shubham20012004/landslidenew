"""Telemetry ingestion: validate -> store -> assess -> Random Forest -> store -> respond -> publish.

Raw readings are committed before inference is attempted, so a failing model
never loses sensor data, and every stored prediction references the exact
reading and model version that produced it.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field

from ml.features import CLASSES, FEATURE_NAMES, extract_features

from .. import repositories as repo
from ..config import Settings
from ..database import Database, iso
from ..logging_setup import kv
from ..schemas import TelemetryIn
from ..serializers import event_api, reading_api
from . import eligibility
from .model_service import ModelService
from .realtime import Hub

log = logging.getLogger("landslide.ingest")

RISK_SEVERITY = {"SAFE": "info", "WATCH": "info", "WARNING": "warning", "CRITICAL": "critical"}
STATUS_SEVERITY = {
    eligibility.CALIBRATING: "info",
    eligibility.INSUFFICIENT_DATA: "info",
    eligibility.MODEL_NOT_READY: "warning",
    eligibility.SENSOR_FAULT: "warning",
    eligibility.INFERENCE_ERROR: "warning",
}


@dataclass
class DeviceState:
    """Per-device live state used to detect transitions (kept in memory, seeded from the DB)."""

    boot_id: str | None = None
    last_seq: int | None = None
    session_id: str | None = None
    last_risk: str | None = None
    last_status: str | None = None
    faults: set[str] = field(default_factory=set)
    offline: bool = False


class IngestionService:
    def __init__(self, db: Database, model: ModelService, hub: Hub, settings: Settings):
        self.db, self.model, self.hub, self.settings = db, model, hub, settings
        self._lock = threading.Lock()
        self._states: dict[str, DeviceState] = {}

    # ------------------------------------------------------------ state
    def _state(self, conn, device_id: str) -> DeviceState:
        st = self._states.get(device_id)
        if st is not None:
            return st
        st = DeviceState()
        dev = repo.get_device(conn, device_id)
        if dev is not None:
            st.boot_id, st.last_seq, st.session_id = dev["boot_id"], dev["last_seq"], dev["session_id"]
            if dev["last_reading_pk"]:
                r = repo.get_reading(conn, dev["last_reading_pk"])
                p = repo.get_prediction(conn, dev["last_reading_pk"])
                if r is not None:
                    st.faults = set(json.loads(r["faults"] or "[]"))
                if p is not None:
                    st.last_status = p["status"]
                    st.last_risk = p["risk_state"] if p["status"] == "OK" else None
            st.offline = (time.time() - dev["last_seen"]) > self.settings.device_offline_s
        self._states[device_id] = st
        return st

    def device_states(self) -> dict[str, DeviceState]:
        return self._states

    # ----------------------------------------------------------- ingest
    def ingest(self, t: TelemetryIn, remote_addr: str | None) -> tuple[dict, bool]:
        """Returns (response for the device, created). created=False for duplicates."""
        now = time.time()
        validated = t.model_dump(by_alias=True)
        features = extract_features(validated)
        published_events: list[dict] = []

        with self._lock:
            with self.db.transaction() as conn:
                existing = repo.reading_pk_by_id(conn, t.reading_id)
                if existing is not None:
                    pred = repo.get_prediction(conn, existing)
                    row = repo.get_reading(conn, existing)
                    log.info("duplicate reading %s", kv(reading_id=t.reading_id, device=t.device_id))
                    return self._device_response("duplicate", t, pred, bool(row["is_live"]) if row else False, now), False

                repo.ensure_device(conn, t.device_id, t.origin, now)
                st = self._state(conn, t.device_id)
                is_live = self._is_live(t, st)
                session = repo.get_session(conn, t.session_id)
                if session is None:
                    repo.insert_session(conn, session_id=t.session_id, device_id=t.device_id, boot_id=t.boot_id,
                                        origin=t.origin, fw=t.fw, now=now, uptime_ms=t.uptime_ms)
                    trigger = t.cal.trigger if t.cal else None
                    published_events.append(self._event(
                        conn, now, "SESSION_STARTED", "info", t,
                        f"Session {t.session_id} started" + (f" ({trigger})" if trigger else ""),
                        detail={"boot_id": t.boot_id, "fw": t.fw, "origin": t.origin}))
                previous_cal = session["calibration_status"] if session is not None else None

                pk = repo.insert_reading(conn, self._reading_row(t, validated, features, now, is_live))
                cal = t.cal.model_dump() if t.cal else None
                repo.update_session(conn, t.session_id, now, cal)
                repo.touch_device(conn, t.device_id, now, remote_addr)
                if is_live:
                    repo.update_device_live(conn, t.device_id, fw=t.fw, boot_id=t.boot_id, session_id=t.session_id,
                                            seq=t.seq, rssi=t.rssi, reading_pk=pk)
                if cal and cal["status"] != previous_cal:
                    ev = self._calibration_event(conn, now, t, cal, pk)
                    if ev:
                        published_events.append(ev)
                if st.offline and is_live:
                    published_events.append(self._event(conn, now, "DEVICE_ONLINE", "info", t,
                                                        f"{t.device_id} is sending telemetry again", reading_pk=pk))
                    st.offline = False

            # ---- inference (outside the raw-data transaction)
            assessment = eligibility.assess(t, features, self.model.ready, self.model.reason)
            pred_row = self._predict(pk, features, assessment, now)
            try:
                with self.db.transaction() as conn:
                    repo.insert_prediction(conn, pred_row)
                    if is_live:
                        published_events += self._live_transition_events(conn, now, t, st, pred_row, pk)
            except Exception:  # noqa: BLE001 - keep the stored reading, report no prediction
                log.exception("failed to store prediction %s", kv(reading_id=t.reading_id))
                pred_row = {**pred_row, "status": eligibility.INFERENCE_ERROR, "risk_state": None,
                            "reason": "prediction could not be stored"}

            if is_live:
                st.boot_id, st.last_seq, st.session_id = t.boot_id, t.seq, t.session_id

        response = self._device_response("success", t, pred_row, is_live, now)
        self._publish(pk, is_live, published_events, now)
        return response, True

    # --------------------------------------------------------- helpers
    @staticmethod
    def _is_live(t: TelemetryIn, st: DeviceState) -> bool:
        """Buffered (replayed) or out-of-order readings never replace the current state."""
        if t.replay:
            return False
        if st.boot_id == t.boot_id and st.last_seq is not None and t.seq <= st.last_seq:
            return False
        return True

    @staticmethod
    def _reading_row(t: TelemetryIn, validated: dict, features: dict, now: float, is_live: bool) -> dict:
        row = {
            "reading_id": t.reading_id,
            "device_id": t.device_id,
            "session_id": t.session_id,
            "boot_id": t.boot_id,
            "seq": t.seq,
            "received_at": now,
            "device_uptime_ms": t.uptime_ms,
            "device_epoch_ms": t.epoch_ms,
            "is_replay": int(t.replay),
            "is_live": int(is_live),
            "origin": t.origin,
            "fw_version": t.fw,
            "telemetry_schema": t.telemetry_schema,
            "feature_schema": t.feature_schema,
            "op_state": t.op_state,
            "calibration_status": t.cal.status if t.cal else None,
            "rssi": t.rssi,
            "faults": json.dumps(sorted(set(t.faults))),
            "soil_raw": t.soil.raw,
            "water_raw": t.water.raw,
            "soil_health": t.soil.health,
            "water_health": t.water.health,
            "mpu_health": t.mpu.health,
            "vib_health": t.vib.health,
            "incl_deg": t.mpu.incl_deg,
            "vib_bg_eps": t.vib.bg_eps,
            "quality_flags": json.dumps({"soil": t.soil.flags, "water": t.water.flags, "mpu": t.mpu.flags}),
            "payload": json.dumps(validated, separators=(",", ":"), allow_nan=False),
        }
        row.update({f: features[f] for f in FEATURE_NAMES})
        return row

    def _predict(self, pk: int, features: dict, a: eligibility.Assessment, now: float) -> dict:
        row = {"reading_pk": pk, "created_at": now, "status": a.status, "reason": a.reason,
               "data_quality": a.data_quality, "model_version": self.model.active_version,
               "model_provenance": self.model.provenance}
        if a.status != eligibility.OK:
            return row
        start = time.perf_counter()
        try:
            pred = self.model.predict(features)
        except Exception as exc:  # noqa: BLE001
            log.exception("inference failed %s", kv(reading_pk=pk))
            row.update(status=eligibility.INFERENCE_ERROR, reason=f"inference failed: {type(exc).__name__}")
            return row
        row.update(
            risk_state=pred.risk_state,
            confidence=pred.confidence,
            latency_ms=round((time.perf_counter() - start) * 1000, 3),
            **{f"p_{c.lower()}": pred.probabilities[c] for c in CLASSES},
        )
        return row

    def _event(self, conn, now, kind, severity, t: TelemetryIn, message, *, reading_pk=None, from_state=None,
               to_state=None, detail=None) -> dict:
        eid = repo.insert_event(conn, now=now, kind=kind, severity=severity, message=message, device_id=t.device_id,
                                session_id=t.session_id, reading_pk=reading_pk, from_state=from_state,
                                to_state=to_state, detail=detail)
        return {"id": eid, "created_at": now, "kind": kind, "severity": severity, "device_id": t.device_id,
                "session_id": t.session_id, "reading_pk": reading_pk, "from_state": from_state, "to_state": to_state,
                "message": message, "detail": json.dumps(detail) if detail else None}

    def _calibration_event(self, conn, now, t: TelemetryIn, cal: dict, pk: int) -> dict | None:
        if cal["status"] == "OK":
            detail = {k: cal.get(k) for k in ("incl0_deg", "accel_std_g", "gyro_std_dps", "vib_bg_eps", "prev_shift_deg",
                                              "attempts", "nvs_saved", "trigger")}
            shift = cal.get("prev_shift_deg")
            msg = f"Calibration OK: initial inclination {cal.get('incl0_deg'):.2f} deg" if cal.get("incl0_deg") is not None \
                else "Calibration OK"
            if shift is not None:
                msg += f"; {shift:.2f} deg from the previous saved baseline"
            return self._event(conn, now, "CALIBRATION_OK", "info", t, msg, reading_pk=pk, detail=detail)
        if cal["status"] == "FAILED":
            return self._event(conn, now, "CALIBRATION_FAILED", "warning", t,
                               f"Calibration failed: {cal.get('error') or 'unknown'} after {cal.get('attempts')} attempts",
                               reading_pk=pk, detail={"error": cal.get("error"), "attempts": cal.get("attempts")})
        return None

    def _live_transition_events(self, conn, now, t: TelemetryIn, st: DeviceState, pred: dict, pk: int) -> list[dict]:
        events = []
        new_session = st.session_id is not None and st.session_id != t.session_id
        if new_session:
            st.last_risk, st.last_status = None, None
        # sensor/device faults
        faults = set(t.faults)
        for f in sorted(faults - st.faults):
            events.append(self._event(conn, now, "FAULT_RAISED", "warning", t, f"Fault raised: {f}", reading_pk=pk,
                                      to_state=f))
        for f in sorted(st.faults - faults):
            events.append(self._event(conn, now, "FAULT_CLEARED", "info", t, f"Fault cleared: {f}", reading_pk=pk,
                                      from_state=f))
        st.faults = faults
        # prediction availability
        status = pred["status"]
        if status != st.last_status:
            if status == eligibility.OK:
                if st.last_status is not None:
                    events.append(self._event(conn, now, "PREDICTION_AVAILABLE", "info", t,
                                              "Random Forest predictions available", reading_pk=pk,
                                              from_state=st.last_status, to_state=status))
            else:
                events.append(self._event(conn, now, "PREDICTION_UNAVAILABLE", STATUS_SEVERITY.get(status, "warning"), t,
                                          f"No prediction: {status}" + (f" ({pred.get('reason')})" if pred.get("reason") else ""),
                                          reading_pk=pk, from_state=st.last_status, to_state=status))
            st.last_status = status
        # risk transitions
        if status == eligibility.OK:
            risk = pred["risk_state"]
            if risk != st.last_risk:
                conf = pred.get("confidence")
                msg = f"Risk {st.last_risk} -> {risk}" if st.last_risk else f"Risk {risk}"
                if conf is not None:
                    msg += f" (model confidence {conf:.2f})"
                events.append(self._event(conn, now, "RISK_CHANGED", RISK_SEVERITY[risk], t, msg, reading_pk=pk,
                                          from_state=st.last_risk, to_state=risk,
                                          detail={"model_version": pred.get("model_version"),
                                                  "provenance": pred.get("model_provenance")}))
                st.last_risk = risk
        return events

    def _device_response(self, status: str, t: TelemetryIn, pred, is_live: bool, now: float) -> dict:
        p = dict(pred) if pred is not None else {}
        ok = p.get("status") == eligibility.OK
        return {
            "status": status,
            "device_id": t.device_id,
            "session_id": t.session_id,
            "reading_id": t.reading_id,
            "is_live": is_live,
            "prediction_available": ok,
            "prediction_status": p.get("status", "NOT_PROCESSED"),
            "risk_state": p.get("risk_state") if ok else None,
            "confidence": p.get("confidence") if ok else None,
            "probabilities": {c: p.get(f"p_{c.lower()}") for c in CLASSES} if ok else None,
            "model_version": p.get("model_version") if ok else None,
            "model_provenance": p.get("model_provenance") if ok else None,
            "prediction_source": "random_forest" if ok else None,
            "data_quality": p.get("data_quality"),
            "predicted_at": iso(p.get("created_at")),
            "server_time": iso(now),
            "reason": p.get("reason"),
        }

    def _publish(self, pk: int, is_live: bool, events: list[dict], now: float) -> None:
        if is_live:
            with self.db.read() as conn:
                r = repo.get_reading(conn, pk)
                p = repo.get_prediction(conn, pk)
            if r is not None:
                self.hub.publish("reading", reading_api(r, p, self.settings.prediction_fresh_s, now))
        for ev in events:
            self.hub.publish("event", event_api(ev))

    # ------------------------------------------------- connectivity checks
    def check_offline(self) -> list[dict]:
        """Marks devices offline when telemetry stops (called periodically)."""
        now = time.time()
        published = []
        with self.db.read() as conn:
            devices = repo.list_devices(conn)
        for d in devices:
            silent = now - d["last_seen"]
            with self._lock:
                st = self._states.get(d["device_id"])
                if st is None or st.offline or silent <= self.settings.device_offline_s:
                    continue
                st.offline = True
                with self.db.transaction() as conn:
                    eid = repo.insert_event(conn, now=now, kind="DEVICE_OFFLINE", severity="warning",
                                            message=f"No telemetry from {d['device_id']} for {silent:.0f} s",
                                            device_id=d["device_id"], session_id=d["session_id"])
                published.append({"id": eid, "created_at": now, "kind": "DEVICE_OFFLINE", "severity": "warning",
                                  "device_id": d["device_id"], "session_id": d["session_id"], "reading_pk": None,
                                  "from_state": None, "to_state": None,
                                  "message": f"No telemetry from {d['device_id']} for {silent:.0f} s", "detail": None})
                log.warning("device offline %s", kv(device=d["device_id"], silent_s=round(silent)))
        for ev in published:
            self.hub.publish("event", event_api(ev))
        return published

    def record_rejection(self, device_id: str | None, kind: str, message: str, detail: dict | None = None) -> None:
        """Persist a communication fault (rejected/invalid upload), rate-limited per device and kind."""
        key = (device_id or "-", kind)
        now = time.time()
        last = getattr(self, "_last_rejection", {})
        if now - last.get(key, 0) < 60:
            return
        last[key] = now
        self._last_rejection = last
        with self.db.transaction() as conn:
            eid = repo.insert_event(conn, now=now, kind=kind, severity="warning", message=message, device_id=device_id,
                                    detail=detail)
        self.hub.publish("event", event_api({"id": eid, "created_at": now, "kind": kind, "severity": "warning",
                                             "device_id": device_id, "session_id": None, "reading_pk": None,
                                             "from_state": None, "to_state": None, "message": message,
                                             "detail": json.dumps(detail) if detail else None}))
