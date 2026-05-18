"""
Idea — Tabelle: ideas
Zentrale Tabelle für alle vom Ideen-Agenten erzeugten Content-Ideen.
Spezifikation: database/data-model.md — Tabelle 1.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.sql import func

from app.database import Base
from app.core.enums import AgentName, ContentType, JobStatus


class Idea(Base):
    __tablename__ = "ideas"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    idea_id = Column(String(20), primary_key=True)              # Format: 3DM-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    title = Column(String(255), nullable=False)
    hook = Column(Text, nullable=False)
    core_emotion = Column(String(100), nullable=False)
    target_group = Column(String(255), nullable=False)
    best_platforms = Column(String(255), nullable=False)        # kommagetrennte Plattformen
    content_type = Column(
        String(20), nullable=False                              # ContentType enum (native_enum=False)
    )
    click_potential = Column(Numeric(3, 1), nullable=False)     # 0.0–10.0
    watchtime_potential = Column(Numeric(3, 1), nullable=False)
    share_potential = Column(Numeric(3, 1), nullable=False)
    status = Column(String(50), nullable=False)                 # JobStatus enum
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(String(50), nullable=False)             # AgentName enum (immer ideen-agent)

    # ── Optionale Felder ───────────────────────────────────────────────────────
    main_scene = Column(Text, nullable=True)
    transformation = Column(Text, nullable=True)
    reveal = Column(Text, nullable=True)
    cta_style = Column(String(255), nullable=True)
    production_complexity = Column(String(20), nullable=True)   # low | medium | high
    risk_level = Column(String(20), nullable=True)              # low | medium | high
    short_description = Column(Text, nullable=True)
    trend_reference = Column(String(20), nullable=True)
    hook_id = Column(String(20), nullable=True)
    slack_message_id = Column(
        String(50),
        ForeignKey("slack_messages.message_id", ondelete="SET NULL"),
        nullable=True,
    )
    approved_by = Column(String(100), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    archived_reason = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)

    # ── Soft Delete ────────────────────────────────────────────────────────────
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # ── Zeitstempel ───────────────────────────────────────────────────────────
    sent_to_slack_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # ── Indizes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_ideas_status", "status"),
        Index("ix_ideas_is_deleted", "is_deleted"),
        Index("ix_ideas_created_at", "created_at"),
    )
