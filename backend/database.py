"""SQLite access: one serialized writer connection, short-lived reader connections.

WAL mode lets the dashboard read while telemetry is being written.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import migrations

log = logging.getLogger("landslide.db")


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Database:
    def __init__(self, path: Path | str, backup_dir: Path | None = None):
        self.path = str(path)
        self.memory = self.path == ":memory:"
        self.backup_dir = Path(backup_dir) if backup_dir else None
        if not self.memory:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = self._connect()

    def _connect(self, read_only: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        if not self.memory and not read_only:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        if read_only:
            conn.execute("PRAGMA query_only = ON")
        return conn

    # ------------------------------------------------------------------ schema
    def migrate(self) -> list[int]:
        with self._lock:
            pending = migrations.pending(self._conn)
            if not pending:
                return []
            has_tables = self._conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            if has_tables and not self.memory and self.backup_dir:
                dest = self.backup(reason="pre-migration")
                log.info("database backed up before migration to %s", dest)
            applied = migrations.apply_all(self._conn, time.time())
            log.info("applied migrations %s", applied)
            return applied

    def schema_version(self) -> int:
        with self.read() as conn:
            versions = migrations.applied_versions(conn)
        return max(versions) if versions else 0

    # ------------------------------------------------------------ transactions
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serialized write transaction; rolls back on any exception."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Read-only connection (separate from the writer when the DB is a file)."""
        if self.memory:
            with self._lock:
                yield self._conn
            return
        conn = self._connect(read_only=True)
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ backup
    def backup(self, reason: str = "manual", dest: Path | None = None) -> Path:
        if self.memory:
            raise RuntimeError("cannot back up an in-memory database")
        if dest is None:
            if not self.backup_dir:
                raise RuntimeError("no backup directory configured")
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dest = self.backup_dir / f"landslide-{stamp}-{reason}.db"
        dest.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(dest))
        try:
            with self._lock:
                self._conn.backup(target)
        finally:
            target.close()
        return dest

    def ping(self) -> bool:
        try:
            with self.read() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()
