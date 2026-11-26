"""Versioned SQLite schema migrations.

Each migration runs once, in order, inside a transaction, and is recorded in
``schema_migrations``. Existing data is never deleted: the tables of the
previous (rule-engine) system are renamed to ``legacy_*_v1`` and kept.
"""
from __future__ import annotations

import sqlite3
from typing import Callable

from ml.features import FEATURE_NAMES


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _execute_script(conn: sqlite3.Connection, script: str) -> None:
    # sqlite3.executescript() would COMMIT the surrounding migration transaction,
    # so statements are executed one by one (the DDL here has no ';' in literals).
    for statement in script.split(";"):
        if statement.strip():
            conn.execute(statement)


def m0001_preserve_legacy(conn: sqlite3.Connection) -> None:
    """Rename the v1 tables (weighted rule engine era) so their data is preserved."""
    if _table_exists(conn, "readings") and "risk_score" in _columns(conn, "readings"):
        for table in ("readings", "events", "experiments"):
            if _table_exists(conn, table):
                conn.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}_v1")
        for (index,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name IN "
            "('idx_readings_time','idx_readings_source','idx_events_time')"
        ).fetchall():
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_legacy_readings_v1_time ON legacy_readings_v1(server_time)")


def m0002_core_schema(conn: sqlite3.Connection) -> None:
    feature_cols = ",\n  ".join(f"{name} REAL" for name in FEATURE_NAMES)
    _execute_script(
        conn,
        f"""
CREATE TABLE devices (
  device_id TEXT PRIMARY KEY,
  origin TEXT NOT NULL,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  fw_version TEXT,
  boot_id TEXT,
  session_id TEXT,
  last_seq INTEGER,
  rssi INTEGER,
  remote_addr TEXT,
  last_reading_pk INTEGER
);

CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL REFERENCES devices(device_id),
  boot_id TEXT NOT NULL,
  origin TEXT NOT NULL,
  fw_version TEXT,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL,
  first_uptime_ms INTEGER,
  calibration_status TEXT,
  calibration_trigger TEXT,
  calibration_error TEXT,
  calibration_json TEXT,
  reading_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ix_sessions_device ON sessions(device_id, first_seen);

CREATE TABLE readings (
  id INTEGER PRIMARY KEY,
  reading_id TEXT NOT NULL UNIQUE,
  device_id TEXT NOT NULL REFERENCES devices(device_id),
  session_id TEXT NOT NULL REFERENCES sessions(session_id),
  boot_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  received_at REAL NOT NULL,
  device_uptime_ms INTEGER,
  device_epoch_ms INTEGER,
  is_replay INTEGER NOT NULL,
  is_live INTEGER NOT NULL,
  origin TEXT NOT NULL,
  fw_version TEXT,
  telemetry_schema TEXT,
  feature_schema TEXT,
  op_state TEXT,
  calibration_status TEXT,
  rssi INTEGER,
  faults TEXT NOT NULL,
  soil_raw INTEGER,
  water_raw INTEGER,
  soil_health TEXT,
  water_health TEXT,
  mpu_health TEXT,
  vib_health TEXT,
  incl_deg REAL,
  vib_bg_eps REAL,
  {feature_cols},
  quality_flags TEXT,
  payload TEXT NOT NULL
);
CREATE INDEX ix_readings_time ON readings(received_at);
CREATE INDEX ix_readings_device_time ON readings(device_id, received_at);
CREATE INDEX ix_readings_session_seq ON readings(session_id, seq);

CREATE TABLE predictions (
  id INTEGER PRIMARY KEY,
  reading_pk INTEGER NOT NULL UNIQUE REFERENCES readings(id) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  status TEXT NOT NULL,
  risk_state TEXT,
  confidence REAL,
  p_safe REAL,
  p_watch REAL,
  p_warning REAL,
  p_critical REAL,
  model_version TEXT,
  model_provenance TEXT,
  data_quality TEXT,
  reason TEXT,
  latency_ms REAL
);
CREATE INDEX ix_predictions_risk ON predictions(risk_state, created_at);

CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  created_at REAL NOT NULL,
  kind TEXT NOT NULL,
  severity TEXT NOT NULL,
  device_id TEXT,
  session_id TEXT,
  reading_pk INTEGER,
  from_state TEXT,
  to_state TEXT,
  message TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX ix_events_time ON events(created_at);
CREATE INDEX ix_events_device_time ON events(device_id, created_at);

CREATE TABLE annotations (
  id INTEGER PRIMARY KEY,
  created_at REAL NOT NULL,
  device_id TEXT NOT NULL,
  session_id TEXT,
  start_time REAL NOT NULL,
  end_time REAL NOT NULL,
  label TEXT NOT NULL CHECK (label IN ('SAFE','WATCH','WARNING','CRITICAL')),
  notes TEXT,
  author TEXT,
  CHECK (end_time > start_time)
);
CREATE INDEX ix_annotations_device_time ON annotations(device_id, start_time, end_time);

CREATE TABLE training_runs (
  id INTEGER PRIMARY KEY,
  created_at REAL NOT NULL,
  model_version TEXT NOT NULL UNIQUE,
  provenance TEXT NOT NULL,
  dataset_sha256 TEXT,
  n_samples INTEGER,
  status TEXT NOT NULL,
  metrics_json TEXT,
  notes TEXT
);
"""
    )


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "preserve legacy v1 tables", m0001_preserve_legacy),
    (2, "four-sensor core schema", m0002_core_schema),
]
LATEST_VERSION = MIGRATIONS[-1][0]


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    if not _table_exists(conn, "schema_migrations"):
        return set()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def pending(conn: sqlite3.Connection) -> list[tuple[int, str, Callable]]:
    done = applied_versions(conn)
    return [m for m in MIGRATIONS if m[0] not in done]


def apply_all(conn: sqlite3.Connection, now: float) -> list[int]:
    """Apply pending migrations; each one in its own transaction."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)"
    )
    applied = []
    for version, name, func in pending(conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            func(conn)
            conn.execute("INSERT INTO schema_migrations (version, name, applied_at) VALUES (?,?,?)", (version, name, now))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied
