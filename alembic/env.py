"""
Alembic Migrations-Umgebung.
DATABASE_URL aus app.config übernehmen — nie aus alembic.ini lesen.
Alle Models müssen importiert sein damit Base.metadata vollständig ist.
"""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Projektverzeichnis in sys.path einfügen
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.config import settings
from app.database import Base

# Alle Models importieren — notwendig für autogenerate
import app.models  # noqa — lädt alle 12 Model-Klassen via __init__.py

# ── Alembic-Konfiguration ──────────────────────────────────────────────────────

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── Offline-Modus (SQL-Dump ohne DB-Verbindung) ───────────────────────────────

def run_migrations_offline() -> None:
    """
    Migrations-SQL generieren ohne Live-Datenbankverbindung.
    Nützlich für Staging-Deploys oder Review.
    """
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online-Modus (direkte DB-Verbindung) ──────────────────────────────────────

def run_migrations_online() -> None:
    """
    Migrations direkt gegen die Datenbank ausführen.
    Standardmodus in Entwicklung und Produktion.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
