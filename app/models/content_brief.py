"""
ContentBrief — SQLAlchemy ORM-Model für content_briefs.
Pydantic-Modell ContentBriefRecord für Service-Layer-Rückgaben.

Spezifikation:
  database/content-briefs-table.md
  content-later/content-brief-versioning.md

Primärschlüssel-Konzept:
  brief_uuid  UUID   → Wahrer PK, pro Version eindeutig
  brief_id    VARCHAR → Familien-ID, geteilt von allen Versionen
  UNIQUE(brief_id, version)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey,
    Index, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.sql import func

from app.database import Base


# ─────────────────────────────────────────────────────────────────────────────
# SQLAlchemy ORM-Model
# ─────────────────────────────────────────────────────────────────────────────

class ContentBrief(Base):
    __tablename__ = "content_briefs"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    brief_uuid = Column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default="gen_random_uuid()",
    )

    # ── Familien-ID + Version ──────────────────────────────────────────────────
    brief_id  = Column(String(20), nullable=False, index=True)   # BRIEF-YYYY-NNN
    version   = Column(Integer, nullable=False, default=1)

    # ── Versionierungskette ────────────────────────────────────────────────────
    parent_version_id = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("content_briefs.brief_uuid", ondelete="RESTRICT"),
        nullable=True,
    )
    rollback_source_uuid = Column(
        PG_UUID(as_uuid=True),
        ForeignKey("content_briefs.brief_uuid", ondelete="SET NULL"),
        nullable=True,
    )
    is_latest_version = Column(Boolean, nullable=False, default=True)
    is_superseded     = Column(Boolean, nullable=False, default=False)
    ab_variant        = Column(String(5), nullable=True)   # 'A' / 'B' / NULL

    # ── Brief-Inhalt ───────────────────────────────────────────────────────────
    brief_json = Column(JSONB, nullable=False)
    change_log = Column(JSONB, nullable=False, server_default="'[]'::jsonb")

    # ── Denormalisierte Schnellzugriff-Felder ──────────────────────────────────
    hook                  = Column(Text, nullable=True)
    primary_emotion       = Column(String(100), nullable=True)
    story_structure       = Column(String(50), nullable=True)
    scene_count           = Column(Integer, nullable=True)
    duration_sec          = Column(Integer, nullable=True)
    visual_style          = Column(String(100), nullable=True)
    camera_style          = Column(String(100), nullable=True)
    audio_style           = Column(String(100), nullable=True)
    cta_style             = Column(String(50), nullable=True)
    platform_format       = Column(JSONB, nullable=True)
    production_complexity = Column(Numeric(3, 1), nullable=True)
    production_risk       = Column(Numeric(3, 1), nullable=True)

    # ── Relationen ─────────────────────────────────────────────────────────────
    idea_id  = Column(String(20), ForeignKey("ideas.idea_id", ondelete="RESTRICT"), nullable=True)
    platform = Column(String(20), nullable=True)

    # ── Validierungsstatus (Sync via Trigger aus content_brief_validations) ─────
    validation_status   = Column(String(30), nullable=False, default="pending")
    validation_score    = Column(Numeric(4, 2), nullable=True)
    validation_notes    = Column(Text, nullable=True)
    validation_errors   = Column(JSONB, nullable=False, server_default="'[]'::jsonb")
    validation_warnings = Column(JSONB, nullable=False, server_default="'[]'::jsonb")
    validated_by        = Column(String(100), nullable=True)
    validated_at        = Column(DateTime(timezone=True), nullable=True)

    # ── Freigabe ───────────────────────────────────────────────────────────────
    approval_status     = Column(String(30), nullable=False, default="PENDING_VALIDATION")
    approved_by         = Column(String(100), nullable=True)
    approved_at         = Column(DateTime(timezone=True), nullable=True)
    rejection_reason    = Column(Text, nullable=True)
    revision_reason     = Column(Text, nullable=True)

    # ── Produktionssperre ──────────────────────────────────────────────────────
    production_blocked     = Column(Boolean, nullable=False, default=True)
    production_started_at  = Column(DateTime(timezone=True), nullable=True)

    # ── FINAL-Status ───────────────────────────────────────────────────────────
    is_final     = Column(Boolean, nullable=False, default=False)
    finalized_at = Column(DateTime(timezone=True), nullable=True)
    finalized_by = Column(String(100), nullable=True)

    # ── Archivierung ───────────────────────────────────────────────────────────
    archived        = Column(Boolean, nullable=False, default=False)
    archived_at     = Column(DateTime(timezone=True), nullable=True)
    archived_reason = Column(String(100), nullable=True)

    # ── Soft-Delete ────────────────────────────────────────────────────────────
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    # ── Metadaten ──────────────────────────────────────────────────────────────
    created_by_agent  = Column(String(100), nullable=True)
    schema_version    = Column(String(20), nullable=False, default="1.0.0")
    compliance_note   = Column(Text, nullable=True)
    notes             = Column(Text, nullable=True)
    created_at        = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    # ── Indizes + Constraints ──────────────────────────────────────────────────
    __table_args__ = (
        UniqueConstraint("brief_id", "version", name="uq_content_briefs_brief_id_version"),
        Index("ix_content_briefs_brief_id", "brief_id"),
        Index("ix_content_briefs_is_latest", "is_latest_version"),
        Index("ix_content_briefs_platform", "platform"),
        Index("ix_content_briefs_approval_status", "approval_status"),
        Index("ix_content_briefs_production_blocked", "production_blocked"),
        Index("ix_content_briefs_is_deleted", "is_deleted"),
        Index("ix_content_briefs_created_at", "created_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic-Modell für Service-Layer (Read-only Rückgabe)
# ─────────────────────────────────────────────────────────────────────────────

class ContentBriefRecord(BaseModel):
    """
    Pydantic-Repräsentation eines content_briefs-Datensatzes.
    Wird von BriefVersionManager zurückgegeben.
    """
    brief_uuid:    UUID
    brief_id:      str
    version:       int
    parent_version_id:    UUID | None
    rollback_source_uuid: UUID | None
    is_latest_version:    bool
    is_superseded:        bool
    ab_variant:           str | None

    brief_json:    dict[str, Any]
    change_log:    list[dict[str, Any]] = Field(default_factory=list)

    # Denormalisierte Felder
    hook:                  str | None
    primary_emotion:       str | None
    story_structure:       str | None
    scene_count:           int | None
    duration_sec:          int | None
    visual_style:          str | None
    camera_style:          str | None
    audio_style:           str | None
    cta_style:             str | None
    platform_format:       dict | None
    production_complexity: float | None
    production_risk:       float | None

    # Relationen
    idea_id:  str | None
    platform: str | None

    # Validierung
    validation_status:    str
    validation_score:     float | None
    validation_notes:     str | None
    validation_errors:    list[dict] = Field(default_factory=list)
    validation_warnings:  list[dict] = Field(default_factory=list)
    validated_by:         str | None
    validated_at:         datetime | None

    # Freigabe
    approval_status:  str
    approved_by:      str | None
    approved_at:      datetime | None
    rejection_reason: str | None
    revision_reason:  str | None

    # Produktionssperre
    production_blocked:    bool
    production_started_at: datetime | None

    # FINAL
    is_final:     bool
    finalized_at: datetime | None
    finalized_by: str | None

    # Archiv
    archived:        bool
    archived_at:     datetime | None
    archived_reason: str | None

    # Soft-Delete
    is_deleted: bool
    deleted_at: datetime | None

    # Meta
    created_by_agent: str | None
    schema_version:   str
    compliance_note:  str | None
    notes:            str | None
    created_at:       datetime
    updated_at:       datetime | None

    model_config = {"from_attributes": True}

    @field_validator("change_log", "validation_errors", "validation_warnings", mode="before")
    @classmethod
    def none_to_empty_list(cls, v):
        """NULL-Werte aus der DB werden zu leeren Listen normalisiert."""
        return v if v is not None else []

    @property
    def can_create_new_version(self) -> bool:
        """Gibt True zurück wenn auf dieser Version eine neue Version aufgebaut werden darf."""
        return not self.is_final and not self.is_deleted

    @property
    def can_be_archived(self) -> bool:
        return not self.is_final and not self.is_deleted

    @property
    def can_rollback_to(self) -> bool:
        return not self.is_final and not self.is_deleted
