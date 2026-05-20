#!/usr/bin/env python3
"""
Railway Migration Script.

Behandelt zwei Fälle:
  1. Frische DB (keine Tabellen): läuft 0001 + 0002
  2. Bestehende DB ohne alembic-Tracking (ideas-Tabelle existiert, aber keine
     alembic_version): stampelt auf 0001, läuft dann 0002

Aufruf: python scripts/migrate.py
"""

import os
import sys

# Projektverzeichnis in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    # Railway / Heroku liefern manchmal postgres:// statt postgresql://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def main() -> None:
    url = get_db_url()
    if not url:
        print("ERROR: DATABASE_URL nicht gesetzt", flush=True)
        sys.exit(1)

    from sqlalchemy import create_engine, inspect
    from alembic.config import Config
    from alembic import command

    engine = create_engine(url)
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    cfg = Config("alembic.ini")

    has_alembic_version   = "alembic_version" in existing_tables
    has_ideas             = "ideas" in existing_tables
    has_content_briefs    = "content_briefs" in existing_tables

    if not has_alembic_version and has_ideas:
        if has_content_briefs:
            # 0002 lief schon halb durch — Stamp direkt auf 0002
            print("content_briefs existiert bereits. Stampe auf 0002 …", flush=True)
            command.stamp(cfg, "0002")
            print("Gestempelt auf 0002 — keine Migration nötig.", flush=True)
            return
        else:
            # Tabellen aus 0001 existieren, aber kein Alembic-Tracking.
            print("Bestehende DB ohne Alembic-Tracking erkannt. Stampe auf 0001 …", flush=True)
            command.stamp(cfg, "0001")
            print("Gestempelt auf 0001.", flush=True)

    print("Führe alembic upgrade head aus …", flush=True)
    command.upgrade(cfg, "head")
    print("Migration abgeschlossen.", flush=True)


if __name__ == "__main__":
    main()
