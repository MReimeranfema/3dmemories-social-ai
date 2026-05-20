"""Add missing columns to content_briefs to match ContentBriefRecord model.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-20

Nutzt ALTER TABLE ... ADD COLUMN IF NOT EXISTS — vollständig idempotent.
"""

from alembic import op
from sqlalchemy import text

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Alle fehlenden Spalten als (name, type, default) Tupel
_MISSING_COLUMNS = [
    # Validierung
    ("validation_score",        "NUMERIC(4,2)",          None),
    ("validation_notes",        "TEXT",                  None),
    ("validation_errors",       "JSONB",                 "'[]'::jsonb"),
    ("validated_at",            "TIMESTAMPTZ",           None),
    ("validated_by",            "VARCHAR(100)",          None),
    # Freigabe
    ("approved_by",             "VARCHAR(100)",          None),
    ("approved_at",             "TIMESTAMPTZ",           None),
    ("rejection_reason",        "TEXT",                  None),
    # Produktion
    ("production_started_at",   "TIMESTAMPTZ",           None),
    # Finalisierung
    ("finalized_at",            "TIMESTAMPTZ",           None),
    ("finalized_by",            "VARCHAR(100)",          None),
    # Archivierung
    ("archived",                "BOOLEAN",               "FALSE"),
    ("archived_at",             "TIMESTAMPTZ",           None),
    ("archived_reason",         "VARCHAR(100)",          None),
    # Soft-Delete
    ("deleted_at",              "TIMESTAMPTZ",           None),
    # Diverses
    ("notes",                   "TEXT",                  None),
    ("updated_at",              "TIMESTAMPTZ",           None),
]


def upgrade() -> None:
    conn = op.get_bind()

    for col_name, col_type, col_default in _MISSING_COLUMNS:
        if col_default is not None:
            sql = (
                f"ALTER TABLE content_briefs "
                f"ADD COLUMN IF NOT EXISTS {col_name} {col_type} DEFAULT {col_default}"
            )
        else:
            sql = (
                f"ALTER TABLE content_briefs "
                f"ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            )
        conn.execute(text(sql))


def downgrade() -> None:
    # Spalten werden beim Downgrade nicht entfernt (Datenverlust-Schutz)
    pass
