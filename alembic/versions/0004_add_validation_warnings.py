"""Add validation_warnings column to content_briefs.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-20
"""

from alembic import op
from sqlalchemy import text

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text(
        "ALTER TABLE content_briefs "
        "ADD COLUMN IF NOT EXISTS validation_warnings JSONB DEFAULT '[]'::jsonb"
    ))


def downgrade() -> None:
    pass
