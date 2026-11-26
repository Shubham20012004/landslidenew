"""SQLite storage for readings, events and experiments."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

READING_COLUMNS = [
    ("server_time", "REAL"), ("server_time_iso", "TEXT"),
    ("device_id", "TEXT"), ("seq", "INTEGER"), ("source", "TEXT"), ("fw_version", "TEXT"),
    ("device_uptime_ms", "INTEGER"), ("device_time_ms", "REAL"), ("latency_ms", "REAL"),
    ("soil_raw", "INTEGER"), ("rain_raw", "INTEGER"), ("rain_do_wet", "INTEGER"),
    ("moisture", "REAL"), ("rain", "REAL"), ("rain_duration_s", "REAL"),
    ("tilt_deg", "REAL"), ("tilt_abs_deg", "REAL"), ("tilt_vec_change_deg", "REAL"),
    ("moisture_rate_per_min", "REAL"), ("tilt_rate_deg_per_min", "REAL"),
    ("comp_m", "REAL"), ("comp_r", "REAL"), ("comp_t", "REAL"),
    ("risk_score", "REAL"), ("risk_state", "TEXT"), ("raw_state", "TEXT"),
    ("health", "TEXT"), ("soil_status", "TEXT"), ("rain_status", "TEXT"), ("mpu_status", "TEXT"),
    ("rates_ready", "INTEGER"), ("faults", "TEXT"),
    ("dropped_count", "INTEGER"), ("buffered", "INTEGER"), ("wifi_rssi", "INTEGER"),
    ("experiment_id", "INTEGER"), ("scenario", "TEXT"), ("dataset_label", "TEXT"),
    ("ml_state", "TEXT"), ("ml_confidence", "REAL"), ("ml_disagree", "INTEGER"), ("ml_note", "TEXT"),
]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  {", ".join(f"{n} {t}" for n, t in READING_COLUMNS)}
);
CREATE INDEX IF NOT EXISTS idx_readings_time ON readings(server_time);
CREATE INDEX IF NOT EXISTS idx_readings_source ON readings(source, server_time);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  server_time REAL, server_time_iso TEXT, device_id TEXT, source TEXT, kind TEXT,
  from_state TEXT, to_state TEXT, risk_score REAL, detail TEXT, experiment_id INTEGER, reading_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(server_time);
CREATE TABLE IF NOT EXISTS experiments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, scenario TEXT, notes TEXT, started_at REAL, stopped_at REAL
);
"""


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            if path != ":memory:":
                self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def close(self):
        with self.lock:
            self.conn.close()

    # ---- readings ----
    def insert_reading(self, row: dict) -> int:
        cols = [c for c, _ in READING_COLUMNS]
        vals = []
        for c in cols:
            v = row.get(c)
            if c == "faults":
                v = json.dumps(v or [])
            elif isinstance(v, bool):
                v = int(v)
            vals.append(v)
        with self.lock:
            cur = self.conn.execute(
                f"INSERT INTO readings ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals
            )
            self.conn.commit()
            return cur.lastrowid

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        if "faults" in d:
            try:
                d["faults"] = json.loads(d["faults"] or "[]")
            except (TypeError, ValueError):
                d["faults"] = []
        for b in ("rain_do_wet", "rates_ready", "ml_disagree"):
            if b in d and d[b] is not None:
                d[b] = bool(d[b])
        return d

    def _source_clause(self, source: str | None):
        if source in ("device", "simulated"):
            return " AND source = ?", [source]
        return "", []

    def latest(self, source: str | None = None) -> dict | None:
        clause, args = self._source_clause(source)
        with self.lock:
            r = self.conn.execute(f"SELECT * FROM readings WHERE 1=1{clause} ORDER BY id DESC LIMIT 1", args).fetchone()
        return self._row(r) if r else None

    def history(self, minutes: float, source: str | None = None, limit: int = 5000) -> list[dict]:
        since = time.time() - minutes * 60
        clause, args = self._source_clause(source)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM (SELECT * FROM readings WHERE server_time >= ?{clause} ORDER BY id DESC LIMIT ?) ORDER BY id",
                [since, *args, limit],
            ).fetchall()
        return [self._row(r) for r in rows]

    def all_readings(self, source: str | None = None, experiment_id: int | None = None) -> list[dict]:
        clause, args = self._source_clause(source)
        if experiment_id is not None:
            clause += " AND experiment_id = ?"
            args.append(experiment_id)
        with self.lock:
            rows = self.conn.execute(f"SELECT * FROM readings WHERE 1=1{clause} ORDER BY id", args).fetchall()
        return [self._row(r) for r in rows]

    def count(self, source: str | None = None) -> int:
        clause, args = self._source_clause(source)
        with self.lock:
            return self.conn.execute(f"SELECT COUNT(*) FROM readings WHERE 1=1{clause}", args).fetchone()[0]

    def last_for_stream(self, device_id: str, source: str) -> dict | None:
        with self.lock:
            r = self.conn.execute(
                "SELECT * FROM readings WHERE device_id=? AND source=? ORDER BY id DESC LIMIT 1", (device_id, source)
            ).fetchone()
        return self._row(r) if r else None

    # ---- events ----
    def insert_event(self, **ev) -> int:
        t = ev.get("server_time", time.time())
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO events (server_time, server_time_iso, device_id, source, kind, from_state, to_state,"
                " risk_score, detail, experiment_id, reading_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (t, iso(t), ev.get("device_id"), ev.get("source"), ev["kind"], ev.get("from_state"),
                 ev.get("to_state"), ev.get("risk_score"), ev.get("detail"), ev.get("experiment_id"),
                 ev.get("reading_id")),
            )
            self.conn.commit()
            return cur.lastrowid

    def events(self, limit: int = 100, source: str | None = None) -> list[dict]:
        clause, args = self._source_clause(source)
        if source in ("device", "simulated"):
            clause = " AND (source = ? OR source IS NULL)"
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM events WHERE 1=1{clause} ORDER BY id DESC LIMIT ?", [*args, limit]
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- experiments ----
    def active_experiment(self) -> dict | None:
        with self.lock:
            r = self.conn.execute(
                "SELECT * FROM experiments WHERE stopped_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(r) if r else None

    def start_experiment(self, name: str, scenario: str, notes: str | None) -> dict:
        now = time.time()
        with self.lock:
            self.conn.execute("UPDATE experiments SET stopped_at=? WHERE stopped_at IS NULL", (now,))
            cur = self.conn.execute(
                "INSERT INTO experiments (name, scenario, notes, started_at) VALUES (?,?,?,?)",
                (name, scenario, notes, now),
            )
            self.conn.commit()
            eid = cur.lastrowid
        return {"id": eid, "name": name, "scenario": scenario, "notes": notes, "started_at": now, "stopped_at": None}

    def stop_experiment(self) -> dict | None:
        exp = self.active_experiment()
        if not exp:
            return None
        now = time.time()
        with self.lock:
            self.conn.execute("UPDATE experiments SET stopped_at=? WHERE id=?", (now, exp["id"]))
            self.conn.commit()
        exp["stopped_at"] = now
        return exp

    def experiments(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM experiments ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]
