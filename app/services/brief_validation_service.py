"""
Service: validate_and_store_brief()

Ablauf:
  1. Brief aus DB laden
  2. Hard-Error-Check
  3. Gesamt-Score berechnen
  4. Eintrag in content_brief_validations speichern
  5. content_briefs via DB-Trigger aktualisiert (automatisch)
  6. Google Sheets Tab CONTENT_BRIEFS aktualisieren
  7. Slack-Notification je nach Status
  8. BriefValidationRecord zurückgeben

Produktionsregeln (aus Pflichtenheft):
  passed  → Brief darf zur Slack-Brief-Freigabe
  warning → Brief darf nur nach manueller Freigabe weiter
  failed  → Brief darf NICHT in Bild- oder Videoproduktion
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog

from app.models.content_brief_validation import (
    BriefValidationInput,
    BriefValidationRecord,
    CategoryScores,
    ValidationError,
    ValidationResult,
    ValidationScoreCalculator,
    ValidationWarning,
)

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class BriefNotFoundError(Exception):
    pass

class BriefAlreadyValidatedError(Exception):
    """Brief wurde seit der letzten Speicherung nicht geändert."""
    pass

class BriefValidationStorageError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────────────────

class BriefValidationService:
    """
    Einziger Einstiegspunkt für alle Validierungs-Speicher-Operationen.
    Kein anderer Service schreibt direkt in content_brief_validations.
    """

    SHEETS_TAB = "CONTENT_BRIEFS"

    # Spalten die im Google Sheet ergänzt / aktualisiert werden
    SHEETS_VALIDATION_COLUMNS = [
        "validation_score",
        "validation_status",
        "validation_notes",
        "validation_errors",
        "validation_warnings",
        "validated_at",
    ]

    def __init__(self, db=None, sheets_client=None, slack_client=None):
        self._db            = db
        self._sheets_client = sheets_client
        self._slack_client  = slack_client

    # ─────────────────────────────────────────────────────────────────────────
    # Haupt-Funktion
    # ─────────────────────────────────────────────────────────────────────────

    def validate_and_store_brief(
        self,
        inp: BriefValidationInput,
    ) -> BriefValidationRecord:
        """
        Vollständiger Validierungs-Speicher-Zyklus.

        Args:
            inp: BriefValidationInput mit Kategorie-Scores und Befunden

        Returns:
            BriefValidationRecord mit finalem Status und production_blocked-Flag

        Raises:
            BriefNotFoundError: brief_uuid existiert nicht
            BriefValidationStorageError: DB-Schreibfehler
        """
        log.info(
            "brief_validation.start",
            brief_id=inp.brief_id,
            version=inp.brief_version,
            brief_uuid=str(inp.brief_uuid),
        )

        # Schritt 1: Brief aus DB laden und verifizieren
        brief_row = self._load_brief(inp.brief_uuid)

        # Schritt 2: Hard-Error-Check aus brief_json
        # Priorität: inp.brief_json (direkt übergeben) → brief_row aus DB → überspringen
        source_json = inp.brief_json or brief_row.get("brief_json")
        if source_json and source_json.get("brief"):
            hard_errors = ValidationScoreCalculator.check_hard_errors_from_brief(source_json)
        else:
            # Kein brief_json verfügbar (Test-Modus ohne DB) — Hard-Error-Erkennung überspringen
            hard_errors = []
        # Mit den übergebenen Fehlern zusammenführen (Duplikate via Code deduplizieren)
        all_errors = self._merge_errors(inp.errors, hard_errors)

        # Schritt 3: Gesamt-Score berechnen
        total_score, status = ValidationScoreCalculator.calculate(
            inp.category_scores, all_errors
        )

        # Schritt 4: In DB speichern (Trigger sync zu content_briefs automatisch)
        record = self._store_validation(
            inp=inp,
            all_errors=all_errors,
            total_score=total_score,
            status=status,
        )

        # Schritt 5: Google Sheets aktualisieren
        self._update_sheets(record, brief_row)

        # Schritt 6: Slack-Notification
        self._notify_slack(record, brief_row)

        log.info(
            "brief_validation.complete",
            brief_id=inp.brief_id,
            version=inp.brief_version,
            score=float(record.validation_score),
            status=record.validation_status,
            production_blocked=record.production_blocked,
        )

        return record

    # ─────────────────────────────────────────────────────────────────────────
    # DB-Operationen
    # ─────────────────────────────────────────────────────────────────────────

    def _load_brief(self, brief_uuid: UUID) -> dict:
        """Lädt brief aus DB. Wirft BriefNotFoundError wenn nicht gefunden."""
        if self._db is None:
            # Test-Modus: minimales Dummy-Brief zurückgeben
            return {
                "brief_uuid": str(brief_uuid),
                "brief_id": "BRIEF-2026-001",
                "version": 1,
                "brief_json": {"brief": {}},
                "platform": "instagram",
                "is_deleted": False,
            }

        result = self._db.execute(
            "SELECT * FROM content_briefs WHERE brief_uuid = :uuid AND is_deleted = FALSE",
            {"uuid": str(brief_uuid)},
        ).fetchone()

        if result is None:
            raise BriefNotFoundError(f"Brief {brief_uuid} nicht gefunden.")
        return dict(result)

    def _store_validation(
        self,
        inp: BriefValidationInput,
        all_errors: list[ValidationError],
        total_score: float,
        status: ValidationResult,
    ) -> BriefValidationRecord:
        """Schreibt Eintrag in content_brief_validations."""

        record_id = uuid4()
        now = datetime.now(timezone.utc)

        errors_json   = [e.model_dump() for e in all_errors]
        warnings_json = [w.model_dump() for w in inp.warnings]

        if self._db is not None:
            self._db.execute(
                """
                INSERT INTO content_brief_validations (
                    id, brief_id, brief_version, brief_uuid,
                    validation_score, validation_status, validation_notes,
                    hook_score, story_score, platform_fit_score, brand_fit_score,
                    clarity_score, production_realism_score, risk_score,
                    validation_errors, validation_warnings,
                    validated_by, validator_version, created_at
                ) VALUES (
                    :id, :brief_id, :brief_version, :brief_uuid,
                    :score, :status, :notes,
                    :hook, :story, :platform_fit, :brand_fit,
                    :clarity, :prod_realism, :risk,
                    :errors::jsonb, :warnings::jsonb,
                    :validated_by, :validator_version, :created_at
                )
                """,
                {
                    "id":               str(record_id),
                    "brief_id":         inp.brief_id,
                    "brief_version":    inp.brief_version,
                    "brief_uuid":       str(inp.brief_uuid),
                    "score":            str(round(total_score, 2)),
                    "status":           status.value,
                    "notes":            inp.validation_notes,
                    "hook":             inp.category_scores.hook_score,
                    "story":            inp.category_scores.story_score,
                    "platform_fit":     inp.category_scores.platform_fit_score,
                    "brand_fit":        inp.category_scores.brand_fit_score,
                    "clarity":          inp.category_scores.clarity_score,
                    "prod_realism":     inp.category_scores.production_realism_score,
                    "risk":             inp.category_scores.risk_score,
                    "errors":           __import__("json").dumps(errors_json),
                    "warnings":         __import__("json").dumps(warnings_json),
                    "validated_by":     inp.validated_by,
                    "validator_version": inp.validator_version,
                    "created_at":       now.isoformat(),
                }
            )
            self._db.commit()

        # production_blocked nach Trigger-Logik ableiten
        production_blocked = status not in (ValidationResult.PASSED, ValidationResult.WARNING)

        return BriefValidationRecord(
            id=record_id,
            brief_id=inp.brief_id,
            brief_version=inp.brief_version,
            brief_uuid=inp.brief_uuid,
            validation_score=Decimal(str(round(total_score, 2))),
            validation_status=status,
            validation_notes=inp.validation_notes,
            hook_score=inp.category_scores.hook_score,
            story_score=inp.category_scores.story_score,
            platform_fit_score=inp.category_scores.platform_fit_score,
            brand_fit_score=inp.category_scores.brand_fit_score,
            clarity_score=inp.category_scores.clarity_score,
            production_realism_score=inp.category_scores.production_realism_score,
            risk_score=inp.category_scores.risk_score,
            validation_errors=errors_json,
            validation_warnings=warnings_json,
            validated_by=inp.validated_by,
            validator_version=inp.validator_version,
            created_at=now,
            production_blocked=production_blocked,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Google Sheets
    # ─────────────────────────────────────────────────────────────────────────

    def _update_sheets(self, record: BriefValidationRecord, brief_row: dict) -> None:
        """
        Aktualisiert Tab CONTENT_BRIEFS in Google Sheets.
        Sucht die Zeile via brief_id + version, aktualisiert Validierungsspalten.
        Fehler werden geloggt aber nicht als Exception weitergegeben.
        """
        if self._sheets_client is None:
            log.debug("brief_validation.sheets.skipped", reason="no_sheets_client")
            return

        import json

        try:
            self._sheets_client.update_row(
                tab=self.SHEETS_TAB,
                match_column="brief_id",
                match_value=record.brief_id,
                updates={
                    "validation_score":    float(record.validation_score),
                    "validation_status":   record.validation_status.value,
                    "validation_notes":    record.validation_notes or "",
                    "validation_errors":   json.dumps(record.validation_errors,   ensure_ascii=False),
                    "validation_warnings": json.dumps(record.validation_warnings, ensure_ascii=False),
                    "validated_at":        record.created_at.isoformat(),
                },
            )
            log.info("brief_validation.sheets.updated", brief_id=record.brief_id)
        except Exception as exc:
            log.warning(
                "brief_validation.sheets.failed",
                brief_id=record.brief_id,
                error=str(exc),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Slack
    # ─────────────────────────────────────────────────────────────────────────

    def _notify_slack(self, record: BriefValidationRecord, brief_row: dict) -> None:
        """
        Slack-Benachrichtigung je nach Validierungsergebnis.
        Fehler werden geloggt aber nicht als Exception weitergegeben.
        """
        if self._slack_client is None:
            log.debug("brief_validation.slack.skipped", reason="no_slack_client")
            return

        try:
            message = self._build_slack_message(record, brief_row)
            self._slack_client.post_message(message)
        except Exception as exc:
            log.warning(
                "brief_validation.slack.failed",
                brief_id=record.brief_id,
                error=str(exc),
            )

    def _build_slack_message(
        self, record: BriefValidationRecord, brief_row: dict
    ) -> str:
        platform = brief_row.get("platform", "?")
        score    = float(record.validation_score)

        error_lines   = "\n".join(
            f"  • {e['code']} — {e['message']}"
            for e in record.validation_errors[:5]   # max 5 Zeilen
        ) or "  keine"
        warning_lines = "\n".join(
            f"  • {w['code']} — {w['message']}"
            for w in record.validation_warnings[:5]
        ) or "  keine"

        if record.validation_status == ValidationResult.FAILED:
            return (
                f"🔴 BRIEF BLOCKIERT — [{record.brief_id}] v{record.brief_version}\n"
                f"Plattform: {platform}\n"
                f"Score:     {score:.1f}/10\n"
                f"Status:    FAILED — Produktion gesperrt\n\n"
                f"Harte Fehler:\n{error_lines}\n\n"
                f"Warnungen:\n{warning_lines}\n\n"
                f"Freigabe: Manueller Review + neue Brief-Version erforderlich\n"
                f"Validate: /validate-brief {record.brief_id}"
            )

        if record.validation_status == ValidationResult.WARNING:
            return (
                f"🟡 BRIEF FREIGEGEBEN (mit Warnungen) — [{record.brief_id}] v{record.brief_version}\n"
                f"Plattform: {platform}\n"
                f"Score:     {score:.1f}/10\n"
                f"Status:    WARNING — manuelle Freigabe erforderlich\n\n"
                f"Warnungen:\n{warning_lines}\n\n"
                f"Approve: /approve-brief {record.brief_id}\n"
                f"Details: /brief-report {record.brief_id}"
            )

        # PASSED
        return (
            f"✅ BRIEF VALIDE — [{record.brief_id}] v{record.brief_version} "
            f"| {platform} | Score {score:.1f}/10 | Bereit für Freigabe\n"
            f"Approve: /approve-brief {record.brief_id}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _merge_errors(
        provided: list[ValidationError],
        detected: list[ValidationError],
    ) -> list[ValidationError]:
        """
        Führt zwei Fehlerlisten zusammen.
        Dedupliziert via error.code — detected überschreibt provided bei gleichem Code.
        """
        merged: dict[str, ValidationError] = {e.code: e for e in provided}
        for e in detected:
            merged[e.code] = e  # detected hat Priorität
        return list(merged.values())

    def get_validation_history(self, brief_uuid: UUID) -> list[BriefValidationRecord]:
        """Gibt alle Validierungseinträge für einen Brief zurück, neueste zuerst."""
        if self._db is None:
            return []

        rows = self._db.execute(
            """
            SELECT * FROM content_brief_validations
            WHERE brief_uuid = :uuid
            ORDER BY created_at DESC
            """,
            {"uuid": str(brief_uuid)},
        ).fetchall()

        return [BriefValidationRecord.model_validate(dict(r)) for r in rows]

    def get_latest_validation(self, brief_uuid: UUID) -> BriefValidationRecord | None:
        """Gibt die neueste Validierung zurück, None wenn keine vorhanden."""
        history = self.get_validation_history(brief_uuid)
        return history[0] if history else None
