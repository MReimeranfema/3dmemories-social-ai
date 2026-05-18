"""
Approval — Tabelle: approvals
Alle Freigabe-Entscheidungen für Ideen und Content-Jobs.
Jede Entscheidung ist dauerhaft und auditierbar.
Spezifikation: database/data-model.md — Tabelle 5.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class Approval(Base):
    __tablename__ = "approvals"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    approval_id = Column(String(20), primary_key=True)          # Format: APR-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    object_type = Column(String(20), nullable=False)            # idea | content_job
    object_id = Column(String(30), nullable=False)              # polymorphisch — kein DB-FK
    approval_type = Column(String(30), nullable=False)          # ApprovalType enum
    result = Column(String(30), nullable=False)                 # ApprovalResult enum
    decided_by = Column(String(100), nullable=False)            # Slack-Username
    slack_message_id = Column(
        String(50),
        ForeignKey("slack_messages.message_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # ── Optionale Felder ───────────────────────────────────────────────────────
    reason = Column(Text, nullable=True)
    revision_instructions = Column(Text, nullable=True)
    slack_response_id = Column(String(50), nullable=True)
    response_time_minutes = Column(Integer, nullable=True)
    is_timeout = Column(Boolean, default=False, nullable=True)
    slack_sent_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    # ── Indizes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_approvals_object_id", "object_id"),
        Index("ix_approvals_object_type", "object_type"),
        Index("ix_approvals_result", "result"),
        Index("ix_approvals_created_at", "created_at"),
    )
