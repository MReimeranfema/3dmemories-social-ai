"""
Cost — Tabelle: costs
Finanzielle Auswertung aller API-Aufrufe.
1:1-Beziehung zu api_calls — jeder Call hat genau einen Cost-Eintrag.
Spezifikation: database/data-model.md — Tabelle 7.
"""

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.sql import func

from app.database import Base


class Cost(Base):
    __tablename__ = "costs"

    # ── Primärschlüssel ────────────────────────────────────────────────────────
    cost_id = Column(String(20), primary_key=True)              # Format: CST-YYYY-NNN

    # ── Pflichtfelder ──────────────────────────────────────────────────────────
    call_id = Column(
        String(20),
        ForeignKey("api_calls.call_id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,                                            # 1:1 mit api_calls
    )
    service = Column(String(30), nullable=False)                # ApiService enum
    cost_unit = Column(String(50), nullable=False)              # EUR | USD | credits | tokens | images
    cost_amount = Column(Numeric(12, 6), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # ── Optionale Felder ───────────────────────────────────────────────────────
    content_id = Column(
        String(30),
        ForeignKey("content_jobs.content_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    cost_eur = Column(Numeric(10, 4), nullable=True)
    exchange_rate = Column(Numeric(10, 6), nullable=True)
    billing_period = Column(Date, nullable=True)                # YYYY-MM als DATE
    budget_category = Column(String(50), nullable=True)         # image_gen | video_gen | text_gen | infra
    is_estimated = Column(Boolean, default=False, nullable=True)
    notes = Column(Text, nullable=True)

    # ── Indizes ────────────────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_costs_service", "service"),
        Index("ix_costs_content_id", "content_id"),
        Index("ix_costs_billing_period", "billing_period"),
        Index("ix_costs_created_at", "created_at"),
    )
