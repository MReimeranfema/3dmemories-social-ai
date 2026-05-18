"""
ContentJob — Tabelle: content_jobs
Zentrale Job-Tabelle. Jeder Job repräsentiert die vollständige
Produktionskette für ein einzelnes Content-Stück auf einer Plattform.
Spezifikation: database/data-model.md — Tabelle 2.

Hinweis: primary_asset_id → assets.asset_id ist ein zirkulärer FK.
         Dieser wird mit use_alter=True deferred angelegt.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base


class ContentJob(Base):
    __tablename__ = "content_jobs"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    content_id = Column(String(30), primary_key=True)           # Format: [PLATFORM]-[YYYY]-[NNN]

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    idea_id = Column(
        String(20),
        ForeignKey("ideas.idea_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    platform = Column(String(20), nullable=False)               # Platform enum
    content_type = Column(String(20), nullable=False)           # ContentType enum
    hook = Column(Text, nullable=False)
    target_audience = Column(String(255), nullable=False)
    primary_emotion = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)                 # JobStatus enum
    retry_counter = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(String(50), nullable=False)             # AgentName enum

    # ── Optionale Felder ───────────────────────────────────────────────────────
    brief_content = Column(JSONB, nullable=True)
    platform_agent = Column(String(50), nullable=True)          # AgentName enum
    has_image = Column(Boolean, default=True, nullable=True)
    has_video = Column(Boolean, default=False, nullable=True)
    caption = Column(Text, nullable=True)
    hashtags = Column(Text, nullable=True)
    cta = Column(String(255), nullable=True)
    qc_score = Column(Numeric(3, 1), nullable=True)
    qc_breakdown = Column(JSONB, nullable=True)
    qc_notes = Column(Text, nullable=True)
    no_go_triggered = Column(Boolean, default=False, nullable=True)
    no_go_detail = Column(Text, nullable=True)

    # Zirkulärer FK → assets.asset_id (use_alter — wird nach assets-Tabelle angelegt)
    primary_asset_id = Column(
        String(20),
        ForeignKey(
            "assets.asset_id",
            use_alter=True,
            name="fk_content_jobs_primary_asset_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    drive_folder_id = Column(String(255), nullable=True)
    approved_by = Column(String(100), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    revision_instructions = Column(Text, nullable=True)
    publishing_notes = Column(Text, nullable=True)
    archived_reason = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    # ── Soft Delete ────────────────────────────────────────────────────────────
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # ── Zeitstempel ───────────────────────────────────────────────────────────
    brief_created_at = Column(DateTime(timezone=True), nullable=True)
    generation_started_at = Column(DateTime(timezone=True), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)
    qc_started_at = Column(DateTime(timezone=True), nullable=True)
    qc_completed_at = Column(DateTime(timezone=True), nullable=True)
    uploaded_to_drive_at = Column(DateTime(timezone=True), nullable=True)
    logged_to_sheets_at = Column(DateTime(timezone=True), nullable=True)
    sent_to_slack_review_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)   # Phase 3
    archived_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # ── Indizes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_content_jobs_status", "status"),
        Index("ix_content_jobs_idea_id", "idea_id"),
        Index("ix_content_jobs_platform", "platform"),
        Index("ix_content_jobs_is_deleted", "is_deleted"),
        Index("ix_content_jobs_created_at", "created_at"),
    )
