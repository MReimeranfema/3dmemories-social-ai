"""
ApiCall — Tabelle: api_calls
Lückenlose Protokollierung jedes externen API-Aufrufs.
Basis für Kostenkontrolle, Fehleranalyse und Audit.
Spezifikation: database/data-model.md — Tabelle 6.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from app.database import Base


class ApiCall(Base):
    __tablename__ = "api_calls"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    call_id = Column(String(20), primary_key=True)              # Format: API-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    service = Column(String(30), nullable=False)                # ApiService enum
    endpoint = Column(String(500), nullable=False)
    method = Column(String(10), nullable=False)                 # GET | POST | PUT | DELETE
    status_code = Column(Integer, nullable=False)
    success = Column(Boolean, nullable=False)                   # true wenn 2xx
    called_by = Column(String(50), nullable=False)              # AgentName enum
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # ── Optionale Felder ───────────────────────────────────────────────────────
    content_id = Column(
        String(30),
        ForeignKey("content_jobs.content_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    prompt_id = Column(
        String(20),
        ForeignKey("prompts.prompt_id", ondelete="SET NULL"),
        nullable=True,
    )
    request_body_hash = Column(String(64), nullable=True)       # SHA-256 — kein Klartext
    response_summary = Column(Text, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    tokens_input = Column(Integer, nullable=True)               # Claude-API
    tokens_output = Column(Integer, nullable=True)
    images_generated = Column(Integer, nullable=True)           # Gemini
    credits_used = Column(Numeric(10, 4), nullable=True)        # Seedance
    error_message = Column(Text, nullable=True)
    retry_of = Column(
        String(20),
        ForeignKey("api_calls.call_id", ondelete="SET NULL"),  # Self-Join bei Retry
        nullable=True,
    )
    rate_limited = Column(Boolean, default=False, nullable=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    # ── Indizes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_api_calls_service", "service"),
        Index("ix_api_calls_content_id", "content_id"),
        Index("ix_api_calls_success", "success"),
        Index("ix_api_calls_created_at", "created_at"),
    )
