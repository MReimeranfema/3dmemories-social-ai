"""Add content_briefs and content_brief_change_log tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-20

Idempotent: nutzt IF NOT EXISTS für alle Operationen.
"""

from alembic import op
from sqlalchemy import text

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── content_briefs ────────────────────────────────────────────────────────
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS content_briefs (
            brief_uuid              VARCHAR(36)     PRIMARY KEY,
            brief_id                VARCHAR(30)     NOT NULL,
            version                 INTEGER         NOT NULL DEFAULT 1,
            parent_version_id       VARCHAR(36),
            rollback_source_uuid    VARCHAR(36),
            is_latest_version       BOOLEAN         NOT NULL DEFAULT TRUE,
            is_superseded           BOOLEAN         NOT NULL DEFAULT FALSE,
            is_final                BOOLEAN         NOT NULL DEFAULT FALSE,
            is_deleted              BOOLEAN         NOT NULL DEFAULT FALSE,
            ab_variant              VARCHAR(10),
            brief_json              JSONB           NOT NULL,
            change_log              JSONB,
            idea_id                 VARCHAR(20),
            platform                VARCHAR(30),
            hook                    TEXT,
            primary_emotion         VARCHAR(50),
            story_structure         VARCHAR(50),
            scene_count             INTEGER,
            duration_sec            INTEGER,
            visual_style            TEXT,
            camera_style            TEXT,
            audio_style             VARCHAR(50),
            cta_style               VARCHAR(50),
            platform_format         JSONB,
            production_complexity   INTEGER,
            production_risk         INTEGER,
            validation_status       VARCHAR(30)     DEFAULT 'pending',
            approval_status         VARCHAR(30)     DEFAULT 'PENDING_VALIDATION',
            production_blocked      BOOLEAN         NOT NULL DEFAULT TRUE,
            revision_reason         TEXT,
            compliance_note         TEXT,
            schema_version          VARCHAR(10)     DEFAULT '1.0.0',
            created_by_agent        VARCHAR(50),
            created_at              TIMESTAMPTZ,
            updated_at              TIMESTAMPTZ
        )
    """))

    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_content_briefs_brief_id
        ON content_briefs (brief_id)
    """))

    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_content_briefs_idea_id
        ON content_briefs (idea_id)
    """))

    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_content_briefs_latest
        ON content_briefs (brief_id, is_latest_version)
    """))

    # ── content_brief_change_log ──────────────────────────────────────────────
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS content_brief_change_log (
            log_uuid                VARCHAR(36)     PRIMARY KEY,
            brief_id                VARCHAR(30)     NOT NULL,
            from_version            INTEGER,
            to_version              INTEGER         NOT NULL,
            from_uuid               VARCHAR(36),
            to_uuid                 VARCHAR(36)     NOT NULL,
            event_type              VARCHAR(30),
            trigger_code            VARCHAR(50),
            trigger_reason          TEXT,
            actor                   VARCHAR(50),
            diff_json               JSONB,
            rollback_source_uuid    VARCHAR(36),
            created_at              TIMESTAMPTZ
        )
    """))

    conn.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_content_brief_change_log_brief_id
        ON content_brief_change_log (brief_id)
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("DROP TABLE IF EXISTS content_brief_change_log"))
    conn.execute(text("DROP TABLE IF EXISTS content_briefs"))
