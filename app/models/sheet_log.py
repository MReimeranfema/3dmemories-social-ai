"""
SheetLog — Tabelle: sheet_logs
Protokollierung aller Schreibvorgänge in Google Sheets.
Sichert Konsistenz zwischen DB und Sheets-Dashboard.
Polymorphe Beziehung — kein DB-FK auf object_id.
Spezifikation: database/data-model.md — Tabelle 11.
"""

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base


class SheetLog(Base):
    __tablename__ = "sheet_logs"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    log_id = Column(String(20), primary_key=True)               # Format: SHL-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    object_type = Column(String(20), nullable=False)            # idea | content_job | api_call | cost | error
    object_id = Column(String(30), nullable=False)              # polymorphisch — kein DB-FK
    sheet_name = Column(String(100), nullable=False)
    sheet_id = Column(String(100), nullable=False)
    operation = Column(String(20), nullable=False)              # insert | update | delete
    success = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # ── Optionale Felder ───────────────────────────────────────────────────────
    row_id = Column(Integer, nullable=True)
    columns_updated = Column(Text, nullable=True)               # kommagetrennte Spaltenliste
    previous_values = Column(JSONB, nullable=True)
    new_values = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=True)
    is_pending_sync = Column(Boolean, default=False, nullable=True)
    notes = Column(Text, nullable=True)

    # ── Indizes ────────────────────────────────────────────────────────────────
    # is_pending_sync=True ist die Sync-Queue — häufig abgefragt
    __table_args__ = (
        Index("ix_sheet_logs_object_id", "object_id"),
        Index("ix_sheet_logs_is_pending_sync", "is_pending_sync"),
        Index("ix_sheet_logs_success", "success"),
        Index("ix_sheet_logs_created_at", "created_at"),
    )
