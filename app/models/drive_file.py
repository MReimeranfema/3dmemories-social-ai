"""
DriveFile — Tabelle: drive_files
Alle in Google Drive gespeicherten Dateien.
Brücke zwischen Systemdatenbank und externem Speicher.
Spezifikation: database/data-model.md — Tabelle 10.
"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.sql import func

from app.database import Base


class DriveFile(Base):
    __tablename__ = "drive_files"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    file_id = Column(String(20), primary_key=True)              # Format: DRV-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    drive_file_id = Column(String(100), nullable=False, unique=True)    # Google Drive eigene File-ID
    drive_url = Column(Text, nullable=False)
    file_name = Column(String(500), nullable=False)
    folder_path = Column(String(500), nullable=False)
    folder_id = Column(String(100), nullable=False)
    mime_type = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    uploaded_by = Column(String(50), nullable=False)            # AgentName enum (immer asset-manager)

    # ── Optionale Felder ───────────────────────────────────────────────────────
    # Deferred FK — assets-Tabelle muss zuerst existieren
    asset_id = Column(
        String(20),
        ForeignKey(
            "assets.asset_id",
            use_alter=True,
            name="fk_drive_files_asset_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    content_id = Column(
        String(30),
        ForeignKey("content_jobs.content_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    file_size_bytes = Column(BigInteger, nullable=True)
    checksum = Column(String(64), nullable=True)
    is_public = Column(Boolean, default=False, nullable=True)   # immer false
    current_folder = Column(String(100), nullable=True)
    moved_to_approved_at = Column(DateTime(timezone=True), nullable=True)
    moved_to_rejected_at = Column(DateTime(timezone=True), nullable=True)
    moved_to_final_at = Column(DateTime(timezone=True), nullable=True)
    deleted_in_drive = Column(Boolean, default=False, nullable=True)
    deleted_in_drive_at = Column(DateTime(timezone=True), nullable=True)
    last_accessed_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    # ── Indizes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_drive_files_content_id", "content_id"),
        Index("ix_drive_files_asset_id", "asset_id"),
    )
