"""SQL data access. All functions take an open connection; callers own transactions."""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ml.features import FEATURE_NAMES

READING_COLUMNS = [
    "reading_id", "device_id", "session_id", "boot_id", "seq", "received_at", "device_uptime_ms", "device_epoch_ms",
    "is_replay", "is_live", "origin", "fw_version", "telemetry_schema", "feature_schema", "op_state",
    "calibration_status", "rssi", "faults", "soil_raw", "water_raw", "soil_health", "water_health", "mpu_health",
    "vib_health", "incl_deg", "vib_bg_eps", *FEATURE_NAMES, "quality_flags", "payload",
]
PREDICTION_COLUMNS = [
    "reading_pk", "created_at", "status", "risk_state", "confidence", "p_safe", "p_watch", "p_warning", "p_critical",
    "model_version", "model_provenance", "data_quality", "reason", "latency_ms",
]
# Columns returned for tables / CSV (no payload JSON)
LIST_COLUMNS = [c for c in READING_COLUMNS if c not in ("payload",)]


# ---------------------------------------------------------------- devices
def get_device(conn: sqlite3.Connection, device_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()


def list_devices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()


def ensure_device(conn: sqlite3.Connection, device_id: str, origin: str, now: float) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO devices (device_id, origin, first_seen, last_seen) VALUES (?,?,?,?)",
        (device_id, origin, now, now),
    )


def touch_device(conn: sqlite3.Connection, device_id: str, now: float, remote_addr: str | None) -> None:
    conn.execute("UPDATE devices SET last_seen=?, remote_addr=? WHERE device_id=?", (now, remote_addr, device_id))


def update_device_live(conn: sqlite3.Connection, device_id: str, *, fw: str, boot_id: str, session_id: str, seq: int,
                       rssi: int | None, reading_pk: int) -> None:
    conn.execute(
        "UPDATE devices SET fw_version=?, boot_id=?, session_id=?, last_seq=?, rssi=?, last_reading_pk=? WHERE device_id=?",
        (fw, boot_id, session_id, seq, rssi, reading_pk, device_id),
    )


# --------------------------------------------------------------- sessions
def get_session(conn: sqlite3.Connection, session_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()


def insert_session(conn: sqlite3.Connection, *, session_id: str, device_id: str, boot_id: str, origin: str, fw: str,
                   now: float, uptime_ms: int) -> None:
    conn.execute(
        "INSERT INTO sessions (session_id, device_id, boot_id, origin, fw_version, first_seen, last_seen, first_uptime_ms)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (session_id, device_id, boot_id, origin, fw, now, now, uptime_ms),
    )


def update_session(conn: sqlite3.Connection, session_id: str, now: float, cal: dict | None) -> None:
    if cal is not None:
        conn.execute(
            "UPDATE sessions SET last_seen=MAX(last_seen, ?), reading_count=reading_count+1, calibration_status=?,"
            " calibration_trigger=?, calibration_error=?, calibration_json=? WHERE session_id=?",
            (now, cal.get("status"), cal.get("trigger"), cal.get("error"), json.dumps(cal), session_id),
        )
    else:
        conn.execute(
            "UPDATE sessions SET last_seen=MAX(last_seen, ?), reading_count=reading_count+1 WHERE session_id=?",
            (now, session_id),
        )


def list_sessions(conn: sqlite3.Connection, device_id: str | None, limit: int, offset: int) -> list[sqlite3.Row]:
    sql = "SELECT * FROM sessions"
    args: list[Any] = []
    if device_id:
        sql += " WHERE device_id=?"
        args.append(device_id)
    sql += " ORDER BY first_seen DESC LIMIT ? OFFSET ?"
    return conn.execute(sql, [*args, limit, offset]).fetchall()


def session_risk_summary(conn: sqlite3.Connection, session_id: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT COALESCE(p.risk_state, p.status, 'NONE') AS k, COUNT(*) FROM readings r"
        " LEFT JOIN predictions p ON p.reading_pk=r.id WHERE r.session_id=? GROUP BY k",
        (session_id,),
    ).fetchall()
    return {k: n for k, n in rows}


# --------------------------------------------------------------- readings
def reading_pk_by_id(conn: sqlite3.Connection, reading_id: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM readings WHERE reading_id=?", (reading_id,)).fetchone()
    return row[0] if row else None


def insert_reading(conn: sqlite3.Connection, row: dict) -> int:
    values = [row.get(c) for c in READING_COLUMNS]
    cur = conn.execute(
        f"INSERT INTO readings ({','.join(READING_COLUMNS)}) VALUES ({','.join('?' * len(READING_COLUMNS))})", values
    )
    return int(cur.lastrowid)


def get_reading(conn: sqlite3.Connection, pk: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM readings WHERE id=?", (pk,)).fetchone()


def latest_live_reading(conn: sqlite3.Connection, device_id: str | None = None) -> Optional[sqlite3.Row]:
    if device_id:
        row = conn.execute("SELECT last_reading_pk FROM devices WHERE device_id=?", (device_id,)).fetchone()
        pk = row[0] if row else None
    else:
        row = conn.execute(
            "SELECT last_reading_pk FROM devices WHERE last_reading_pk IS NOT NULL ORDER BY last_seen DESC LIMIT 1"
        ).fetchone()
        pk = row[0] if row else None
    return get_reading(conn, pk) if pk else None


@dataclass
class ReadingFilter:
    device_id: Optional[str] = None
    session_id: Optional[str] = None
    start: Optional[float] = None
    end: Optional[float] = None
    risk_state: Optional[str] = None  # SAFE/WATCH/WARNING/CRITICAL or UNAVAILABLE
    origin: Optional[str] = None

    def where(self, alias: str = "r", pred_alias: str = "p") -> tuple[str, list[Any]]:
        clauses, args = [], []
        if self.device_id:
            clauses.append(f"{alias}.device_id = ?")
            args.append(self.device_id)
        if self.session_id:
            clauses.append(f"{alias}.session_id = ?")
            args.append(self.session_id)
        if self.start is not None:
            clauses.append(f"{alias}.received_at >= ?")
            args.append(self.start)
        if self.end is not None:
            clauses.append(f"{alias}.received_at < ?")
            args.append(self.end)
        if self.origin:
            clauses.append(f"{alias}.origin = ?")
            args.append(self.origin)
        if self.risk_state == "UNAVAILABLE":
            clauses.append(f"({pred_alias}.status IS NULL OR {pred_alias}.status != 'OK')")
        elif self.risk_state:
            clauses.append(f"{pred_alias}.risk_state = ?")
            args.append(self.risk_state)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def reading_page(conn: sqlite3.Connection, flt: ReadingFilter, limit: int, before_pk: int | None) -> list[sqlite3.Row]:
    where, args = flt.where()
    if before_pk is not None:
        where = (where + " AND " if where else " WHERE ") + "r.id < ?"
        args.append(before_pk)
    cols = ", ".join(f"r.{c}" for c in LIST_COLUMNS)
    pcols = ", ".join(f"p.{c} AS pred_{c}" for c in PREDICTION_COLUMNS if c != "reading_pk")
    sql = (
        f"SELECT r.id, {cols}, {pcols} FROM readings r LEFT JOIN predictions p ON p.reading_pk = r.id"
        f"{where} ORDER BY r.id DESC LIMIT ?"
    )
    return conn.execute(sql, [*args, limit]).fetchall()


def count_readings(conn: sqlite3.Connection, flt: ReadingFilter) -> int:
    where, args = flt.where()
    return conn.execute(
        f"SELECT COUNT(*) FROM readings r LEFT JOIN predictions p ON p.reading_pk = r.id{where}", args
    ).fetchone()[0]


SERIES_FIELDS = ("soil_moisture", "water_wetness", "tilt_deg", "vib_events_per_s")


def reading_series(conn: sqlite3.Connection, flt: ReadingFilter, max_points: int) -> dict:
    """Chart data. Long ranges are averaged into time buckets (AVG ignores NULL,
    so a bucket without valid measurements stays NULL: gaps are never filled)."""
    where, args = flt.where()
    span = conn.execute(
        f"SELECT MIN(r.received_at), MAX(r.received_at), COUNT(*) FROM readings r"
        f" LEFT JOIN predictions p ON p.reading_pk = r.id{where}",
        args,
    ).fetchone()
    t0, t1, n = span
    if not n:
        return {"bucket_s": None, "points": []}
    start = flt.start if flt.start is not None else t0
    end = flt.end if flt.end is not None else t1
    bucket = max(1.0, math.ceil((end - start) / max(1, max_points)))
    if n <= max_points:
        rows = conn.execute(
            f"SELECT r.received_at AS t, {', '.join('r.' + f for f in SERIES_FIELDS)} FROM readings r"
            f" LEFT JOIN predictions p ON p.reading_pk = r.id{where} ORDER BY r.received_at",
            args,
        ).fetchall()
        return {"bucket_s": None, "points": [dict(r) for r in rows]}
    rows = conn.execute(
        f"SELECT CAST(r.received_at / ? AS INTEGER) * ? + ? / 2.0 AS t, "
        + ", ".join(f"AVG(r.{f}) AS {f}" for f in SERIES_FIELDS)
        + f" FROM readings r LEFT JOIN predictions p ON p.reading_pk = r.id{where}"
        " GROUP BY CAST(r.received_at / ? AS INTEGER) ORDER BY t",
        [bucket, bucket, bucket, *args, bucket],
    ).fetchall()
    return {"bucket_s": bucket, "points": [dict(r) for r in rows]}


def iter_export_rows(conn: sqlite3.Connection, flt: ReadingFilter) -> Iterable[sqlite3.Row]:
    where, args = flt.where()
    cols = ", ".join(f"r.{c}" for c in LIST_COLUMNS)
    pcols = ", ".join(f"p.{c} AS pred_{c}" for c in PREDICTION_COLUMNS if c != "reading_pk")
    cur = conn.execute(
        f"SELECT r.id, {cols}, {pcols} FROM readings r LEFT JOIN predictions p ON p.reading_pk = r.id"
        f"{where} ORDER BY r.id",
        args,
    )
    while True:
        rows = cur.fetchmany(500)
        if not rows:
            break
        yield from rows


# ------------------------------------------------------------ predictions
def insert_prediction(conn: sqlite3.Connection, row: dict) -> int:
    values = [row.get(c) for c in PREDICTION_COLUMNS]
    cur = conn.execute(
        f"INSERT INTO predictions ({','.join(PREDICTION_COLUMNS)}) VALUES ({','.join('?' * len(PREDICTION_COLUMNS))})",
        values,
    )
    return int(cur.lastrowid)


def get_prediction(conn: sqlite3.Connection, reading_pk: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM predictions WHERE reading_pk=?", (reading_pk,)).fetchone()


# ----------------------------------------------------------------- events
def insert_event(conn: sqlite3.Connection, *, now: float, kind: str, severity: str, message: str,
                 device_id: str | None = None, session_id: str | None = None, reading_pk: int | None = None,
                 from_state: str | None = None, to_state: str | None = None, detail: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO events (created_at, kind, severity, device_id, session_id, reading_pk, from_state, to_state,"
        " message, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (now, kind, severity, device_id, session_id, reading_pk, from_state, to_state, message,
         json.dumps(detail) if detail else None),
    )
    return int(cur.lastrowid)


def list_events(conn: sqlite3.Connection, *, device_id: str | None, session_id: str | None, kinds: list[str] | None,
                severities: list[str] | None, start: float | None, end: float | None, limit: int,
                before_id: int | None) -> list[sqlite3.Row]:
    clauses, args = [], []
    for col, val in (("device_id", device_id), ("session_id", session_id)):
        if val:
            clauses.append(f"{col}=?")
            args.append(val)
    if kinds:
        clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
        args += kinds
    if severities:
        clauses.append(f"severity IN ({','.join('?' * len(severities))})")
        args += severities
    if start is not None:
        clauses.append("created_at >= ?")
        args.append(start)
    if end is not None:
        clauses.append("created_at < ?")
        args.append(end)
    if before_id is not None:
        clauses.append("id < ?")
        args.append(before_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return conn.execute(f"SELECT * FROM events{where} ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()


# ------------------------------------------------------------ annotations
def insert_annotation(conn: sqlite3.Connection, row: dict) -> int:
    cur = conn.execute(
        "INSERT INTO annotations (created_at, device_id, session_id, start_time, end_time, label, notes, author)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (row["created_at"], row["device_id"], row.get("session_id"), row["start_time"], row["end_time"], row["label"],
         row.get("notes"), row.get("author")),
    )
    return int(cur.lastrowid)


def list_annotations(conn: sqlite3.Connection, device_id: str | None = None) -> list[sqlite3.Row]:
    if device_id:
        return conn.execute("SELECT * FROM annotations WHERE device_id=? ORDER BY start_time DESC", (device_id,)).fetchall()
    return conn.execute("SELECT * FROM annotations ORDER BY start_time DESC").fetchall()


def delete_annotation(conn: sqlite3.Connection, annotation_id: int) -> bool:
    return conn.execute("DELETE FROM annotations WHERE id=?", (annotation_id,)).rowcount > 0


def count_in_range(conn: sqlite3.Connection, device_id: str, session_id: str | None, start: float, end: float) -> int:
    sql = "SELECT COUNT(*) FROM readings WHERE device_id=? AND received_at>=? AND received_at<=?"
    args: list[Any] = [device_id, start, end]
    if session_id:
        sql += " AND session_id=?"
        args.append(session_id)
    return conn.execute(sql, args).fetchone()[0]


# ---------------------------------------------------------- training runs
def record_training_run(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO training_runs (created_at, model_version, provenance, dataset_sha256, n_samples, status,"
        " metrics_json, notes) VALUES (?,?,?,?,?,?,?,?)",
        (row["created_at"], row["model_version"], row["provenance"], row.get("dataset_sha256"), row.get("n_samples"),
         row["status"], row.get("metrics_json"), row.get("notes")),
    )


def list_training_runs(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, created_at, model_version, provenance, dataset_sha256, n_samples, status, notes"
        " FROM training_runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
