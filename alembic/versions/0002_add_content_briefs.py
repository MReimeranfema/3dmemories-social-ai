"""Add content_briefs and content_brief_change_log tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── content_briefs ────────────────────────────────────────────────────────
    op.create_table(
        "content_briefs",
        sa.Column("brief_uuid", sa.String(36), primary_key=True),
        sa.Column("brief_id", sa.String(30), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False, default=1),
        sa.Column("parent_version_id", sa.String(36), nullable=True),
        sa.Column("rollback_source_uuid", sa.String(36), nullable=True),
        sa.Column("is_latest_version", sa.Boolean, nullable=False, default=True),
        sa.Column("is_superseded", sa.Boolean, nullable=False, default=False),
        sa.Column("is_final", sa.Boolean, nullable=False, default=False),
        sa.Column("is_deleted", sa.Boolean, nullable=False, default=False),
        sa.Column("ab_variant", sa.String(10), nullable=True),
        sa.Column("brief_json", postgresql.JSONB, nullable=False),
        sa.Column("change_log", postgresql.JSONB, nullable=True),
        # Denormalisierte Felder
        sa.Column("idea_id", sa.String(20), nullable=True),
        sa.Column("platform", sa.String(30), nullable=True),
        sa.Column("hook", sa.Text, nullable=True),
        sa.Column("primary_emotion", sa.String(50), nullable=True),
        sa.Column("story_structure", sa.String(50), nullable=True),
        sa.Column("scene_count", sa.Integer, nullable=True),
        sa.Column("duration_sec", sa.Integer, nullable=True),
        sa.Column("visual_style", sa.Text, nullable=True),
        sa.Column("camera_style", sa.Text, nullable=True),
        sa.Column("audio_style", sa.String(50), nullable=True),
        sa.Column("cta_style", sa.String(50), nullable=True),
        sa.Column("platform_format", postgresql.JSONB, nullable=True),
        sa.Column("production_complexity", sa.Integer, nullable=True),
        sa.Column("production_risk", sa.Integer, nullable=True),
        # Status
        sa.Column("validation_status", sa.String(30), nullable=True, default="pending"),
        sa.Column("approval_status", sa.String(30), nullable=True, default="PENDING_VALIDATION"),
        sa.Column("production_blocked", sa.Boolean, nullable=False, default=True),
        # Metadaten
        sa.Column("revision_reason", sa.Text, nullable=True),
        sa.Column("compliance_note", sa.Text, nullable=True),
        sa.Column("schema_version", sa.String(10), nullable=True, default="1.0.0"),
        sa.Column("created_by_agent", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_content_briefs_brief_id", "content_briefs", ["brief_id"])
    op.create_index("ix_content_briefs_idea_id", "content_briefs", ["idea_id"])
    op.create_index(
        "ix_content_briefs_latest",
        "content_briefs",
        ["brief_id", "is_latest_version"],
    )

    # ── content_brief_change_log ──────────────────────────────────────────────
    op.create_table(
        "content_brief_change_log",
        sa.Column("log_uuid", sa.String(36), primary_key=True),
        sa.Column("brief_id", sa.String(30), nullable=False, index=True),
        sa.Column("from_version", sa.Integer, nullable=True),
        sa.Column("to_version", sa.Integer, nullable=False),
        sa.Column("from_uuid", sa.String(36), nullable=True),
        sa.Column("to_uuid", sa.String(36), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=True),
        sa.Column("trigger_code", sa.String(50), nullable=True),
        sa.Column("trigger_reason", sa.Text, nullable=True),
        sa.Column("actor", sa.String(50), nullable=True),
        sa.Column("diff_json", postgresql.JSONB, nullable=True),
        sa.Column("rollback_source_uuid", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_content_brief_change_log_brief_id",
        "content_brief_change_log",
        ["brief_id"],
    )


def downgrade() -> None:
    op.drop_table("content_brief_change_log")
    op.drop_table("content_briefs")
