"""
Pydantic Models für Content-Brief-Validierungsspeicherung.
Spezifikation: quality-control/content-brief-validation.md
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class ValidationResult(str, Enum):
    PENDING = "pending"
    PASSED  = "passed"
    WARNING = "warning"
    FAILED  = "failed"


# ─────────────────────────────────────────────────────────────────────────────
# Fehler und Warnungen
# ─────────────────────────────────────────────────────────────────────────────

class ValidationError(BaseModel):
    """Harter Fehler — Brief kann nicht produziert werden."""
    code:        str  = Field(..., description="Fehlercode z.B. 'MISSING_HOOK'")
    field:       str  = Field(..., description="Betroffenes Feld z.B. 'brief.hook.text'")
    message:     str  = Field(..., description="Menschlich lesbarer Fehlertext")
    severity:    str  = Field(default="error", pattern="^error$")

    model_config = {"frozen": True}


class ValidationWarning(BaseModel):
    """Weiche Warnung — Brief kann produziert werden, aber nicht optimal."""
    code:        str  = Field(..., description="Warnungscode z.B. 'HOOK_WEAK'")
    field:       str  = Field(...)
    message:     str  = Field(...)
    severity:    str  = Field(default="warning", pattern="^warning$")
    suggestion:  str  = Field(default="", description="Empfohlene Korrektur")

    model_config = {"frozen": True}


# ─────────────────────────────────────────────────────────────────────────────
# Kategorie-Scores
# ─────────────────────────────────────────────────────────────────────────────

class CategoryScores(BaseModel):
    """
    Scores je Validierungskategorie (0–10).
    Gewichtung in ValidationScoreCalculator.WEIGHTS.
    """
    hook_score:               float = Field(..., ge=0, le=10)
    story_score:              float = Field(..., ge=0, le=10)
    platform_fit_score:       float = Field(..., ge=0, le=10)
    brand_fit_score:          float = Field(..., ge=0, le=10)
    clarity_score:            float = Field(..., ge=0, le=10)
    production_realism_score: float = Field(..., ge=0, le=10)
    risk_score:               float = Field(..., ge=0, le=10)


# ─────────────────────────────────────────────────────────────────────────────
# Input: Validierungsergebnis das gespeichert werden soll
# ─────────────────────────────────────────────────────────────────────────────

class BriefValidationInput(BaseModel):
    """
    Input für validate_and_store_brief().
    Enthält alle Kategorie-Scores und Befunde — der Service berechnet
    den Gesamt-Score und leitet den Status ab.

    brief_json: Optional. Wenn angegeben, wird check_hard_errors_from_brief()
    darauf ausgeführt (zusätzlich zu den übergebenen errors).
    Wenn nicht angegeben und kein DB-Zugriff vorhanden, wird die
    automatische Hard-Error-Erkennung übersprungen.
    """
    brief_uuid:   UUID            = Field(..., description="brief_uuid des zu validierenden Briefs")
    brief_id:     str             = Field(..., pattern=r"^BRIEF-\d{4}-\d{3}$")
    brief_version: int            = Field(..., ge=1)
    category_scores: CategoryScores
    errors:       list[ValidationError]   = Field(default_factory=list)
    warnings:     list[ValidationWarning] = Field(default_factory=list)
    validation_notes: str | None  = None
    validated_by: str             = Field(..., min_length=1)
    validator_version: str        = Field(default="1.0.0")
    brief_json:   dict | None     = Field(default=None, description="Optionaler Brief-JSON für direkte Hard-Error-Erkennung")

    @field_validator("brief_id")
    @classmethod
    def brief_id_format(cls, v: str) -> str:
        import re
        if not re.match(r"^BRIEF-\d{4}-\d{3}$", v):
            raise ValueError(f"brief_id muss Format BRIEF-YYYY-NNN haben, got: {v}")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Output: Gespeicherter Validierungseintrag
# ─────────────────────────────────────────────────────────────────────────────

class BriefValidationRecord(BaseModel):
    """
    Repräsentiert einen Eintrag in content_brief_validations.
    Wird von validate_and_store_brief() zurückgegeben.
    """
    id:                       UUID
    brief_id:                 str
    brief_version:            int
    brief_uuid:               UUID

    validation_score:         Decimal = Field(..., ge=0, le=10)
    validation_status:        ValidationResult
    validation_notes:         str | None

    hook_score:               float
    story_score:              float
    platform_fit_score:       float
    brand_fit_score:          float
    clarity_score:            float
    production_realism_score: float
    risk_score:               float

    validation_errors:        list[dict[str, Any]]
    validation_warnings:      list[dict[str, Any]]

    validated_by:             str
    validator_version:        str
    created_at:               datetime

    # Aus content_briefs nach dem Sync-Trigger
    production_blocked:       bool

    model_config = {"from_attributes": True}

    @property
    def can_proceed_to_production(self) -> bool:
        return self.validation_status in (ValidationResult.PASSED, ValidationResult.WARNING)

    @property
    def requires_manual_approval(self) -> bool:
        return self.validation_status == ValidationResult.WARNING

    @property
    def is_hard_blocked(self) -> bool:
        return self.validation_status == ValidationResult.FAILED


# ─────────────────────────────────────────────────────────────────────────────
# Score-Berechnung
# ─────────────────────────────────────────────────────────────────────────────

class ValidationScoreCalculator:
    """
    Berechnet den gewichteten Gesamt-Score aus den Kategorie-Scores.
    Harte Fehler setzen den Score auf 0 und erzwingen status=failed.
    """

    WEIGHTS: dict[str, float] = {
        "hook_score":               0.25,   # Hook ist kritischster Faktor
        "story_score":              0.15,
        "platform_fit_score":       0.15,
        "brand_fit_score":          0.20,   # Brand-Konformität sehr gewichtet
        "clarity_score":            0.10,
        "production_realism_score": 0.10,
        "risk_score":               0.05,   # Niedrigere Gewichtung — separater Block
    }
    # Summe: 1.00

    # Score-Schwellen (aus Pflichtenheft)
    THRESHOLD_PASSED  = 8.0
    THRESHOLD_WARNING = 6.5

    # Harte Fehler — erzwingen status=failed unabhängig vom Score
    HARD_ERROR_CODES = {
        "MISSING_HOOK",
        "MISSING_PLATFORM",
        "MISSING_CONTENT_TYPE",
        "MISSING_SCENE_STRUCTURE",
        "MISSING_PLATFORM_FORMAT",
        "UNCLEAR_STORY",
        "BRAND_FIT_TOO_LOW",      # brand_fit_score < 5
        "COMPLIANCE_RISK_HIGH",   # risk_score impliziert Compliance-Risiko > 7
    }

    @classmethod
    def calculate(
        cls,
        scores: CategoryScores,
        errors: list[ValidationError],
    ) -> tuple[float, ValidationResult]:
        """
        Gibt (gesamt_score: float, status: ValidationResult) zurück.
        """
        # Harte Fehler → sofortiges failed
        hard_error_codes = {e.code for e in errors}
        has_hard_error = bool(hard_error_codes & cls.HARD_ERROR_CODES)

        # Gewichteter Score
        raw = (
            scores.hook_score               * cls.WEIGHTS["hook_score"] +
            scores.story_score              * cls.WEIGHTS["story_score"] +
            scores.platform_fit_score       * cls.WEIGHTS["platform_fit_score"] +
            scores.brand_fit_score          * cls.WEIGHTS["brand_fit_score"] +
            scores.clarity_score            * cls.WEIGHTS["clarity_score"] +
            scores.production_realism_score * cls.WEIGHTS["production_realism_score"] +
            scores.risk_score               * cls.WEIGHTS["risk_score"]
        )
        total_score = round(raw, 2)

        # Status ableiten
        if has_hard_error or total_score < cls.THRESHOLD_WARNING:
            status = ValidationResult.FAILED
        elif total_score < cls.THRESHOLD_PASSED:
            status = ValidationResult.WARNING
        else:
            status = ValidationResult.PASSED

        return total_score, status

    @classmethod
    def check_hard_errors_from_brief(cls, brief_json: dict) -> list[ValidationError]:
        """
        Schnell-Check: Alle harten Fehler direkt aus brief_json ableiten.
        Ergänzt die kategorie-basierten Befunde.
        """
        errors: list[ValidationError] = []
        brief = brief_json.get("brief", {})

        if not brief.get("hook", {}).get("text"):
            errors.append(ValidationError(
                code="MISSING_HOOK",
                field="brief.hook.text",
                message="Hook-Text fehlt oder ist leer.",
            ))

        if not brief.get("platform"):
            errors.append(ValidationError(
                code="MISSING_PLATFORM",
                field="brief.platform",
                message="Plattform fehlt.",
            ))

        if not brief.get("content_type"):
            errors.append(ValidationError(
                code="MISSING_CONTENT_TYPE",
                field="brief.content_type",
                message="Content-Typ fehlt.",
            ))

        if not brief.get("scene_structure"):
            errors.append(ValidationError(
                code="MISSING_SCENE_STRUCTURE",
                field="brief.scene_structure",
                message="Szenenstruktur fehlt oder ist leer.",
            ))

        if not brief.get("platform_format"):
            errors.append(ValidationError(
                code="MISSING_PLATFORM_FORMAT",
                field="brief.platform_format",
                message="Plattformformat fehlt.",
            ))

        story = brief.get("story_structure", {})
        if story and not story.get("act_1", {}).get("description"):
            errors.append(ValidationError(
                code="UNCLEAR_STORY",
                field="brief.story_structure.act_1.description",
                message="Story-Akt 1 hat keine Beschreibung — Story unklar.",
            ))

        return errors
