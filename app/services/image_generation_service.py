"""
ImageGenerationService — Einstiegspunkt für die Bildgenerierungs-Pipeline.

Spezifikation: image-generation/image-generation-architecture.md

Verantwortlichkeiten:
  1. ContentBrief anhand brief_uuid aus DB laden
  2. Vorbedingungen prüfen (production_blocked, archived, deleted)
  3. Job-ID erzeugen
  4. ImageJobRunner erstellen und ausführen
  5. ImageJobResult zurückgeben

Vorbedingungen (werden hart geprüft — Exception bei Verstoß):
  - Brief existiert in DB
  - production_blocked = FALSE
  - archived = FALSE
  - is_deleted = FALSE

Fehler-Hierarchie:
  BriefNotFoundError      → Brief existiert nicht
  BriefBlockedError       → production_blocked=True oder nicht freigegeben
  BriefArchivedError      → Brief archiviert
  BriefDeletedError       → Brief soft-deleted
  ImageGenerationError    → Generierungsfehler (wraps JobResult.error_message)

Dev-Mode:
  Wenn kein DB-Handle → Dummy-Brief wird verwendet.
  Alle nachgelagerten Services laufen in ihrem eigenen Dev-Mode.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog

from app.config import settings
from app.models.content_brief import ContentBriefRecord
from app.models.image_generation import (
    ImageGenerationRequest,
    ImageJobResult,
    ImageJobStatus,
)
from app.workers.image_job_runner import ImageJobRunner

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class ImageGenerationError(Exception):
    """Basisklasse für alle Fehler im ImageGenerationService."""

class BriefNotFoundError(ImageGenerationError):
    """Brief-UUID nicht in der Datenbank gefunden."""

class BriefBlockedError(ImageGenerationError):
    """Brief ist produktionsgesperrt (production_blocked=True)."""

class BriefArchivedError(ImageGenerationError):
    """Brief ist archiviert und darf nicht mehr produziert werden."""

class BriefDeletedError(ImageGenerationError):
    """Brief ist soft-deleted."""

class BriefValidationError(ImageGenerationError):
    """Brief hat keinen approved Validierungsstatus."""


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktion: Brief aus DB laden
# ─────────────────────────────────────────────────────────────────────────────

def _load_brief_from_db(db, brief_uuid: UUID) -> dict:
    """
    Lädt einen ContentBrief-Datensatz aus der Datenbank.
    Gibt das Row-Dictionary zurück oder None wenn nicht gefunden.
    """
    try:
        row = db.execute(
            """
            SELECT *
            FROM content_briefs
            WHERE brief_uuid = :uuid
              AND is_deleted  = FALSE
            """,
            {"uuid": str(brief_uuid)},
        ).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        log.error(
            "image_generation_service.db_load_failed",
            brief_uuid=str(brief_uuid),
            error=str(exc),
        )
        raise


def _row_to_record(row: dict) -> ContentBriefRecord:
    """
    Konvertiert einen DB-Row-Dict in ein ContentBriefRecord.
    Setzt fehlende optionale Felder auf None/Defaults.
    """
    defaults: dict[str, Any] = {
        "parent_version_id":    None,
        "rollback_source_uuid": None,
        "is_latest_version":    True,
        "is_superseded":        False,
        "ab_variant":           None,
        "change_log":           [],
        "hook":                 None,
        "primary_emotion":      None,
        "story_structure":      None,
        "scene_count":          None,
        "duration_sec":         None,
        "visual_style":         None,
        "camera_style":         None,
        "audio_style":          None,
        "cta_style":            None,
        "platform_format":      None,
        "production_complexity": None,
        "production_risk":      None,
        "idea_id":              None,
        "platform":             None,
        "validation_score":     None,
        "validation_notes":     None,
        "validation_errors":    [],
        "validation_warnings":  [],
        "validated_by":         None,
        "validated_at":         None,
        "approved_by":          None,
        "approved_at":          None,
        "rejection_reason":     None,
        "revision_reason":      None,
        "production_started_at": None,
        "is_final":             False,
        "finalized_at":         None,
        "finalized_by":         None,
        "archived":             False,
        "archived_at":          None,
        "archived_reason":      None,
        "is_deleted":           False,
        "deleted_at":           None,
        "created_by_agent":     None,
        "schema_version":       "1.0.0",
        "compliance_note":      None,
        "notes":                None,
        "updated_at":           None,
    }
    merged = {**defaults, **{k: v for k, v in row.items() if v is not None or k not in defaults}}
    return ContentBriefRecord(**merged)


def _make_dummy_brief(brief_uuid: UUID, platform: str, content_type: str) -> ContentBriefRecord:
    """
    Erzeugt einen minimalen Dummy-Brief für den Dev-Mode (kein DB-Handle).
    Enthält genug Felder damit ImagePromptBuilder + QC durchlaufen können.
    """
    from datetime import datetime as dt

    now = dt.now(timezone.utc)
    dummy_json = {
        "brief": {
            "hook":             "Ein Kind hält eine 3D-Erinnerung in Händen",
            "primary_emotion":  "freude",
            "story_structure":  "emotion_first",
            "visual_style":     "warm_lifestyle",
            "camera_style":     "medium_closeup",
            "cta":              "Jetzt bestellen",
            "brand": {
                "primary_color":    "#F5E6D3",
                "secondary_color":  "#8B7355",
                "style_keywords":   ["warm", "handmade", "emotional", "family"],
            },
        }
    }
    return ContentBriefRecord(
        brief_uuid             = brief_uuid,
        brief_id               = "BRIEF-DEV-001",
        version                = 1,
        parent_version_id      = None,
        rollback_source_uuid   = None,
        is_latest_version      = True,
        is_superseded          = False,
        ab_variant             = None,
        brief_json             = dummy_json,
        change_log             = [],
        hook                   = "Ein Kind hält eine 3D-Erinnerung in Händen",
        primary_emotion        = "freude",
        story_structure        = "emotion_first",
        scene_count            = None,
        duration_sec           = None,
        visual_style           = "warm_lifestyle",
        camera_style           = "medium_closeup",
        audio_style            = None,
        cta_style              = "Jetzt bestellen",
        platform_format        = None,
        production_complexity  = None,
        production_risk        = None,
        idea_id                = None,
        platform               = platform,
        validation_status      = "approved",
        validation_score       = 9.0,
        validation_notes       = None,
        validation_errors      = [],
        validation_warnings    = [],
        validated_by           = "dev-mode",
        validated_at           = now,
        approval_status        = "APPROVED",
        approved_by            = "dev-mode",
        approved_at            = now,
        rejection_reason       = None,
        revision_reason        = None,
        production_blocked     = False,
        production_started_at  = None,
        is_final               = False,
        finalized_at           = None,
        finalized_by           = None,
        archived               = False,
        archived_at            = None,
        archived_reason        = None,
        is_deleted             = False,
        deleted_at             = None,
        created_by_agent       = "dev-mode",
        schema_version         = "1.0.0",
        compliance_note        = None,
        notes                  = None,
        created_at             = now,
        updated_at             = None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Job-ID-Generator
# ─────────────────────────────────────────────────────────────────────────────

def _generate_job_id() -> str:
    """
    Erzeugt eine eindeutige Job-ID.
    Format: IMG-YYYY-{6 Hex-Zeichen Groß} — z.B. IMG-2026-A3F7C1
    """
    year = datetime.now(timezone.utc).year
    suffix = uuid.uuid4().hex[:6].upper()
    return f"IMG-{year}-{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# ImageGenerationService
# ─────────────────────────────────────────────────────────────────────────────

class ImageGenerationService:
    """
    Einstiegspunkt für die Bildgenerierungs-Pipeline.

    Lädt den ContentBrief, prüft alle Vorbedingungen und delegiert
    an den ImageJobRunner für die eigentliche Pipeline-Ausführung.

    Im Dev-Mode (db=None):
      Verwendet Dummy-Brief — alle Services laufen in ihrem Dev-Mode.
      Kein echter API-Call, kein echter Drive-Upload.

    Verwendung:
        svc    = ImageGenerationService(db=session)
        result = svc.generate_image(request)
    """

    def __init__(self, db=None):
        self._db = db

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche API
    # ─────────────────────────────────────────────────────────────────────────

    def generate_image(self, request: ImageGenerationRequest) -> ImageJobResult:
        """
        Führt einen vollständigen Bildgenerierungs-Job aus.

        Args:
            request: ImageGenerationRequest mit brief_uuid, platform, content_type

        Returns:
            ImageJobResult — immer, auch bei Fehler (status=FAILED)

        Raises:
            BriefNotFoundError:   Brief existiert nicht in DB
            BriefBlockedError:    Brief ist produktionsgesperrt
            BriefArchivedError:   Brief ist archiviert
            BriefDeletedError:    Brief ist soft-deleted
            BriefValidationError: Brief hat keinen approved Status
        """
        log.info(
            "image_generation_service.start",
            job_id       = request.job_id,
            brief_uuid   = str(request.brief_uuid),
            platform     = request.platform,
            content_type = request.content_type,
            requested_by = request.requested_by,
        )

        # ── Brief laden ────────────────────────────────────────────────────────
        brief = self._load_and_validate_brief(request)

        # ── Job-Runner ausführen ───────────────────────────────────────────────
        runner = ImageJobRunner(
            brief   = brief,
            request = request,
            db      = self._db,
        )
        result = runner.run()

        log.info(
            "image_generation_service.done",
            job_id   = result.job_id,
            status   = result.status.value,
            cost_eur = round(result.cost_eur_total, 4),
        )

        return result

    def generate_image_for_brief(
        self,
        brief_uuid:   UUID,
        platform:     str,
        content_type: str,
        requested_by: str = "system",
        quality_boost: bool = False,
        force_regenerate: bool = False,
    ) -> ImageJobResult:
        """
        Convenience-Wrapper — erzeugt ImageGenerationRequest und ruft generate_image().

        Vereinfacht Celery-Task und API-Route-Integration.
        """
        request = ImageGenerationRequest(
            brief_uuid       = brief_uuid,
            platform         = platform,
            content_type     = content_type,
            job_id           = _generate_job_id(),
            requested_by     = requested_by,
            quality_boost    = quality_boost,
            force_regenerate = force_regenerate,
        )
        return self.generate_image(request)

    # ─────────────────────────────────────────────────────────────────────────
    # Brief laden + validieren
    # ─────────────────────────────────────────────────────────────────────────

    def _load_and_validate_brief(
        self, request: ImageGenerationRequest
    ) -> ContentBriefRecord:
        """
        Lädt den Brief aus DB und prüft alle Vorbedingungen.

        Dev-Mode (kein DB-Handle): gibt Dummy-Brief zurück.

        Raises:
            BriefNotFoundError, BriefBlockedError, BriefArchivedError,
            BriefDeletedError, BriefValidationError
        """
        if self._db is None:
            log.warning(
                "image_generation_service.dev_mode",
                reason="kein DB-Handle — Dummy-Brief aktiv",
                brief_uuid=str(request.brief_uuid),
            )
            return _make_dummy_brief(
                brief_uuid   = request.brief_uuid,
                platform     = request.platform,
                content_type = request.content_type,
            )

        # DB-Lookup
        row = _load_brief_from_db(self._db, request.brief_uuid)
        if row is None:
            raise BriefNotFoundError(
                f"ContentBrief nicht gefunden: {request.brief_uuid}"
            )

        brief = _row_to_record(row)

        # Soft-Delete
        if brief.is_deleted:
            raise BriefDeletedError(
                f"Brief {brief.brief_id} (v{brief.version}) ist gelöscht."
            )

        # Archiviert
        if brief.archived:
            raise BriefArchivedError(
                f"Brief {brief.brief_id} (v{brief.version}) ist archiviert "
                f"(Grund: {brief.archived_reason}). Kann nicht produziert werden."
            )

        # Produktionssperre
        if brief.production_blocked:
            raise BriefBlockedError(
                f"Brief {brief.brief_id} (v{brief.version}) ist produktionsgesperrt "
                f"(production_blocked=True). Approval-Status: {brief.approval_status}. "
                f"Bitte erst Brief freigeben."
            )

        # Validierungsstatus — nur approved Briefs dürfen in Produktion
        valid_statuses = {"approved", "APPROVED", "approved_with_warnings"}
        if brief.validation_status not in valid_statuses:
            raise BriefValidationError(
                f"Brief {brief.brief_id} hat Validierungsstatus '{brief.validation_status}'. "
                f"Nur Briefs mit Status 'approved' können produziert werden."
            )

        # Platform aus Request überschreibt Brief-Platform wenn gesetzt
        # (Brief-Platform bleibt für Logging, Request-Platform gilt für Generierung)

        log.info(
            "image_generation_service.brief_loaded",
            job_id            = request.job_id,
            brief_id          = brief.brief_id,
            brief_version     = brief.version,
            validation_status = brief.validation_status,
            approval_status   = brief.approval_status,
            platform          = request.platform,
            content_type      = request.content_type,
        )

        return brief


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_image_generation_service: ImageGenerationService | None = None


def get_image_generation_service(db=None) -> ImageGenerationService:
    """
    Gibt eine ImageGenerationService-Instanz zurück.

    Im Produktionsbetrieb mit DB-Session pro Request aufrufen:
        svc = get_image_generation_service(db=session)

    Im Dev-Mode ohne DB:
        svc = get_image_generation_service()
    """
    return ImageGenerationService(db=db)
