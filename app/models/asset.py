"""
Asset — Tabelle: assets
Alle generierten Dateien eines Content-Jobs.
Spezifikation: database/data-model.md — Tabelle 3.

Zirkuläre FKs:
  - content_jobs.primary_asset_id → assets.asset_id  (use_alter in content_job.py)
  - assets.drive_file_id → drive_files.file_id        (use_alter hier)
  - drive_files.asset_id → assets.asset_id            (use_alter in drive_file.py)
  - prompts.asset_id → assets.asset_id                (use_alter in prompt.py)
"""

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from app.database import Base


class Asset(Base):
    __tablename__ = "assets"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    asset_id = Column(String(20), primary_key=True)             # Format: AST-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    content_id = Column(
        String(30),
        ForeignKey("content_jobs.content_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    asset_type = Column(String(20), nullable=False)             # AssetType enum
    file_name = Column(String(500), nullable=False)
    file_format = Column(String(20), nullable=False)            # png | mp4 | txt | json
    status = Column(String(50), nullable=False)                 # generated | uploaded | qc_failed | approved | archived
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(String(50), nullable=False)             # AgentName enum

    # ── Optionale Felder ───────────────────────────────────────────────────────
    file_size_bytes = Column(BigInteger, nullable=True)
    width_px = Column(Integer, nullable=True)
    height_px = Column(Integer, nullable=True)
    duration_seconds = Column(Numeric(5, 2), nullable=True)
    aspect_ratio = Column(String(10), nullable=True)            # z.B. 9:16, 4:5, 1:1
    resolution_label = Column(String(20), nullable=True)        # z.B. 1080p, 4K
    temp_url = Column(Text, nullable=True)

    # Deferred FK — drive_files-Tabelle wird nach assets angelegt
    drive_file_id = Column(
        String(20),
        ForeignKey(
            "drive_files.file_id",
            use_alter=True,
            name="fk_assets_drive_file_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    drive_url = Column(Text, nullable=True)

    # Referenz auf erzeugenden Prompt
    prompt_id = Column(
        String(20),
        ForeignKey("prompts.prompt_id", ondelete="SET NULL"),
        nullable=True,
    )

    version = Column(Integer, default=1, nullable=True)
    is_primary = Column(Boolean, default=False, nullable=True)
    qc_score = Column(Numeric(3, 1), nullable=True)
    qc_notes = Column(Text, nullable=True)

    # ── Soft Delete ────────────────────────────────────────────────────────────
    is_deleted = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # ── Zeitstempel ───────────────────────────────────────────────────────────
    uploaded_at = Column(DateTime(timezone=True), nullable=True)
    qc_checked_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)

    # ── Indizes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_assets_content_id", "content_id"),
        Index("ix_assets_asset_type", "asset_type"),
        Index("ix_assets_status", "status"),
    )
