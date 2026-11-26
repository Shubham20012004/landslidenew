"""Schema migrations, legacy data preservation and backups."""
from __future__ import annotations

import sqlite3

from backend.database import Database
from backend.migrations import LATEST_VERSION


def make_legacy_db(path):
    """Minimal copy of the v1 (rule-engine) schema with data."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE readings (id INTEGER PRIMARY KEY AUTOINCREMENT, server_time REAL, device_id TEXT,
            moisture REAL, risk_score REAL, risk_state TEXT);
        CREATE INDEX idx_readings_time ON readings(server_time);
        CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, server_time REAL, kind TEXT);
        CREATE INDEX idx_events_time ON events(server_time);
        CREATE TABLE experiments (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);
        INSERT INTO readings (server_time, device_id, moisture, risk_score, risk_state) VALUES (1, 'esp32-node-1', 0.4, 0.25, 'WATCH');
        INSERT INTO readings (server_time, device_id, moisture, risk_score, risk_state) VALUES (2, 'esp32-node-1', 0.5, 0.3, 'WATCH');
        INSERT INTO events (server_time, kind) VALUES (1, 'STATE_CHANGE');
        INSERT INTO experiments (name) VALUES ('run 1');
    """)
    c.commit()
    c.close()


def test_fresh_database_migrates_once(tmp_path):
    db = Database(tmp_path / "new.db", tmp_path / "backups")
    assert db.migrate() == [1, 2]
    assert db.migrate() == []
    assert db.schema_version() == LATEST_VERSION
    with db.read() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert not (tmp_path / "backups").exists()  # nothing to back up for an empty database
    db.close()


def test_legacy_data_is_preserved_and_backed_up(tmp_path):
    path = tmp_path / "legacy.db"
    make_legacy_db(path)
    db = Database(path, tmp_path / "backups")
    assert db.migrate() == [1, 2]
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM legacy_readings_v1").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM legacy_events_v1").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM legacy_experiments_v1").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0
        assert "reading_id" in {r[1] for r in conn.execute("PRAGMA table_info(readings)")}
    backups = list((tmp_path / "backups").glob("*pre-migration.db"))
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])
    assert old.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 2
    old.close()
    db.close()


def test_failed_transaction_rolls_back(tmp_path):
    db = Database(tmp_path / "t.db")
    db.migrate()
    try:
        with db.transaction() as conn:
            conn.execute("INSERT INTO devices (device_id, origin, first_seen, last_seen) VALUES ('d', 'device', 1, 1)")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0
    db.close()


def test_manual_backup(tmp_path):
    db = Database(tmp_path / "t.db", tmp_path / "b")
    db.migrate()
    dest = db.backup()
    assert dest.exists() and dest.stat().st_size > 0
    db.close()
