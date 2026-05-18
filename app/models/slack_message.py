"""
SlackMessage — Tabelle: slack_messages
Alle ausgehenden und eingehenden Slack-Nachrichten.
Erstellt zuerst — wird von ideas, approvals, errors referenziert.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base


class SlackMessage(Base):
    __tablename__ = "slack_messages"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    message_id = Column(String(20), primary_key=True)           # SLK-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    slack_internal_id = Column(String(50), nullable=False)      # Slack ts-Wert
    channel = Column(String(100), nullable=False)
    direction = Column(String(10), nullable=False)              # outgoing / incoming
    message_type = Column(String(50), nullable=False)           # idea_review | content_review | error_alert | ...
    object_type = Column(String(20), nullable=False)            # idea | content_job | system
    object_id = Column(String(30), nullable=False)              # polymorphisch — kein DB-FK
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # ── Optionale Felder ───────────────────────────────────────────────────────
    message_body = Column(Text, nullable=True)
    block_payload = Column(JSONB, nullable=True)
    sent_by = Column(String(100), nullable=True)
    response_received = Column(Boolean, default=False, nullable=True)
    response_type = Column(String(50), nullable=True)           # approved | rejected | revision_requested
    response_by = Column(String(100), nullable=True)
    response_text = Column(Text, nullable=True)
    response_at = Column(DateTime(timezone=True), nullable=True)
    response_message_id = Column(String(50), nullable=True)
    timeout_at = Column(DateTime(timezone=True), nullable=True)
    is_timed_out = Column(Boolean, default=False, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    retry_attempt = Column(Integer, default=0, nullable=True)
    notes = Column(Text, nullable=True)
