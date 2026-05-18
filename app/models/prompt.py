"""
Prompt — Tabelle: prompts
Vollständige Dokumentation aller verwendeten Prompts.
Prompts sind unveränderlich — nur neue Versionen werden angelegt.
Spezifikation: database/data-model.md — Tabelle 4.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base


class Prompt(Base):
    __tablename__ = "prompts"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    prompt_id = Column(String(20), primary_key=True)            # Format: PRM-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    content_id = Column(
        String(30),
        ForeignKey("content_jobs.content_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    prompt_type = Column(String(50), nullable=False)            # image_generation | video_generation | caption | hook | brief
    prompt_text = Column(Text, nullable=False)
    target_service = Column(String(30), nullable=False)         # ApiService enum
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by = Column(String(50), nullable=False)             # AgentName enum

    # ── Optionale Felder ───────────────────────────────────────────────────────
    model_version = Column(String(100), nullable=True)          # z.B. gemini-2.5-flash-image
    negative_prompt = Column(Text, nullable=True)
    parameters = Column(JSONB, nullable=True)                   # Temperatur, Steps etc.

    # Referenz auf erzeugtes Asset (use_alter wegen asset → prompt Kreisreferenz)
    asset_id = Column(
        String(20),
        ForeignKey(
            "assets.asset_id",
            use_alter=True,
            name="fk_prompts_asset_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    result_quality = Column(Numeric(3, 1), nullable=True)
    version = Column(Integer, default=1, nullable=True)
    parent_prompt_id = Column(
        String(20),
        ForeignKey("prompts.prompt_id", ondelete="SET NULL"),  # Self-Join bei Revision
        nullable=True,
    )
    revision_reason = Column(Text, nullable=True)
    is_successful = Column(Boolean, nullable=True)
    notes = Column(Text, nullable=True)

    # ── Zeitstempel ───────────────────────────────────────────────────────────
    used_at = Column(DateTime(timezone=True), nullable=True)
