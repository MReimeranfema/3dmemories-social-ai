"""
PerformanceLater — Tabelle: performance_later
Performance-Daten für veröffentlichte Inhalte.

STATUS PHASE 1: Tabelle angelegt — nicht befüllt.
Erst in Phase 3 aktiv wenn Analytics-Agent live geht.
Spezifikation: database/data-model.md — Tabelle 12.
"""

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.sql import func

from app.database import Base


class PerformanceLater(Base):
    __tablename__ = "performance_later"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    performance_id = Column(String(20), primary_key=True)       # Format: PFM-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    content_id = Column(
        String(30),
        ForeignKey("content_jobs.content_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,                                            # 1:1 mit content_jobs
    )
    platform = Column(String(20), nullable=False)               # Platform enum
    measured_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # ── Performance-Metriken (alle optional — Phase 3) ─────────────────────────
    views = Column(BigInteger, nullable=True)
    plays = Column(BigInteger, nullable=True)
    reach = Column(BigInteger, nullable=True)
    impressions = Column(BigInteger, nullable=True)
    likes = Column(Integer, nullable=True)
    comments = Column(Integer, nullable=True)
    shares = Column(Integer, nullable=True)
    saves = Column(Integer, nullable=True)
    follows_gained = Column(Integer, nullable=True)
    profile_visits = Column(Integer, nullable=True)
    link_clicks = Column(Integer, nullable=True)
    watchtime_seconds = Column(BigInteger, nullable=True)
    avg_watchtime_pct = Column(Numeric(5, 2), nullable=True)
    completion_rate = Column(Numeric(5, 2), nullable=True)
    engagement_rate = Column(Numeric(5, 4), nullable=True)      # (likes+comments+saves) / reach
    ctr = Column(Numeric(5, 4), nullable=True)
    hook_retention_pct = Column(Numeric(5, 2), nullable=True)   # % nach 3 Sek.

    # ── Platform-IDs ──────────────────────────────────────────────────────────
    later_post_id = Column(String(100), nullable=True)
    platform_post_id = Column(String(255), nullable=True)

    # ── Meta ───────────────────────────────────────────────────────────────────
    published_at = Column(DateTime(timezone=True), nullable=True)
    data_source = Column(String(50), nullable=True)             # later_api | platform_api | manual
    notes = Column(Text, nullable=True)

    # ── Indizes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_performance_later_platform", "platform"),
        Index("ix_performance_later_measured_at", "measured_at"),
    )
