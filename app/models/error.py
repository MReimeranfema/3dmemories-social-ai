"""
Error — Tabelle: errors
Vollständige Fehlerprotokollierung des gesamten Systems.
Basis für Debugging, Retry-Logik und Systemstabilität.
Spezifikation: database/data-model.md — Tabelle 8.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class Error(Base):
    __tablename__ = "errors"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    error_id = Column(String(20), primary_key=True)             # Format: ERR-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    error_type = Column(String(30), nullable=False)             # ErrorType enum
    error_message = Column(Text, nullable=False)
    object_type = Column(String(20), nullable=False)            # idea | content_job | system
    object_id = Column(String(30), nullable=True)               # NULL bei System-Fehler
    previous_status = Column(String(50), nullable=False)        # Status vor dem Fehler
    reporting_agent = Column(String(50), nullable=False)        # AgentName enum
    retry_counter = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # ── Optionale Felder ───────────────────────────────────────────────────────
    call_id = Column(
        String(20),
        ForeignKey("api_calls.call_id", ondelete="SET NULL"),  # wenn API-Fehler
        nullable=True,
    )
    stack_trace = Column(Text, nullable=True)
    api_status_code = Column(Integer, nullable=True)
    api_error_body = Column(Text, nullable=True)
    resolution = Column(String(50), nullable=True)              # retry | archived | hard_stop | manual_fix | resolved
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_notes = Column(Text, nullable=True)
    slack_alert_sent = Column(Boolean, default=False, nullable=True)
    slack_alert_id = Column(
        String(50),
        ForeignKey("slack_messages.message_id", ondelete="SET NULL"),
        nullable=True,
    )
    is_critical = Column(Boolean, default=False, nullable=True)
    notes = Column(Text, nullable=True)

    # ── Indizes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_errors_error_type", "error_type"),
        Index("ix_errors_object_id", "object_id"),
        Index("ix_errors_object_type", "object_type"),
        Index("ix_errors_is_critical", "is_critical"),
        Index("ix_errors_created_at", "created_at"),
    )
