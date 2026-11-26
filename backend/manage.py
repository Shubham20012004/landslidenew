"""Maintenance commands.

    python -m backend.manage migrate      create/upgrade the database schema (backs up first)
    python -m backend.manage backup       write a consistent copy to backend/data/backups/
    python -m backend.manage check        validate configuration, database and model
"""
from __future__ import annotations

import argparse
import sys

from .config import Settings
from .database import Database
from .services.model_service import ModelService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Landslide backend maintenance")
    parser.add_argument("command", choices=["migrate", "backup", "check"])
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        print("configuration error:", exc, file=sys.stderr)
        return 2
    db = Database(settings.db_path, settings.backup_dir)
    try:
        if args.command == "migrate":
            applied = db.migrate()
            print("database:", settings.db_path)
            print("applied migrations:", applied or "none (already up to date)")
        elif args.command == "backup":
            print("backup written to", db.backup(reason="manual"))
        elif args.command == "check":
            ok = True
            for problem in settings.problems():
                print("WARNING:", problem)
                ok = ok and "LANDSLIDE_DEVICE_TOKENS" not in problem
            pending = db.schema_version()
            print("database schema version:", pending)
            model = ModelService(settings)
            ready = model.load()
            print("model:", "READY " + str(model.active_version) if ready else "NOT READY - " + model.reason)
            return 0 if ok else 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
