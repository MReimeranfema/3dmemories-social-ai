"""
Service: BriefLockService

Verwaltet den Production-Lock (production_blocked) und den FINAL-Status
für Content-Briefs.

Verantwortlichkeiten:
  lock()    → production_blocked = TRUE  (blockiert Produktion)
  unlock()  → production_blocked = FALSE (gibt Produktion frei)
  finalize() → is_final = TRUE           (versiegelt Brief unwiderruflich)
  start_production() → production_started_at setzen

Invarianten:
  - FINAL-Briefs können niemals entsperrt oder verändert werden
  - unlock() ist nur bei approval_status == 'APPROVED' erlaubt
  - finalize() erfordert production_started_at != NULL
  - Alle Operationen schreiben in Sheets + loggen via structlog

Wer ruft Lock/Unlock auf?
  BriefApprovalService.approve()        → unlock()
  BriefApprovalService.reject()         → lock() (implizit, da approval blockiert)
  ImageJobRunner.run()                  → start_production()
  ImageJobRunner (nach erfolgreichem Job) → finalize()
  Operator-Aktion                       → lock() / unlock() manuell
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import structlog

from app.models.content_brief import ContentBriefRecord
from app.services.brief_version_manager import (
    BriefVersionManager,
    BriefFinalError,
    BriefNotFoundError,
)

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class LockError(Exception):
    """Allgemeiner Lock-Fehler."""

class UnlockNotAllowedError(LockError):
    """unlock() verweigert — Brief ist nicht freigegeben."""

class AlreadyFinalError(LockError):
    """finalize() verweigert — Brief ist bereits FINAL."""

class ProductionNotStartedError(LockError):
    """finalize() verweigert — Produktion noch nicht gestartet."""


# ─────────────────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────────────────

class BriefLockService:
    """
    Verwaltet production_blocked-Flag und FINAL-Status.

    Ohne DB → Dev-Modus (nur Logging, keine DB-Schreibvorgänge).
    Mit DB  → vollständiger Produktionspfad.
    """

    SHEETS_TAB = "CONTENT_BRIEFS_LOCK_LOG"

    def __init__(
        self,
        db=None,
        version_manager: Optional[BriefVersionManager] = None,
        sheets_client=None,
    ):
        self._db              = db
        self._version_manager = version_manager or BriefVersionManager(db=db)
        self._sheets_client   = sheets_client

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche API
    # ─────────────────────────────────────────────────────────────────────────

    def lock(self, brief_id: str, actor: str, reason: Optional[str] = None) -> None:
        """
        Setzt production_blocked = TRUE.
        Auch bei APPROVED-Status erlaubt (z.B. nachträgliche Compliance-Sperre).

        Args:
            brief_id: Brief-Familien-ID
            actor:    Wer sperrt (Agent-Name oder Operator-Username)
            reason:   Optionaler Grund
        """
        log.info("brief_lock.lock", brief_id=brief_id, actor=actor, reason=reason)

        record = self._load_and_check(brief_id)
        now    = datetime.now(timezone.utc)

        if self._db is not None:
            self._db.execute(
                """
                UPDATE content_briefs
                SET production_blocked = TRUE,
                    updated_at         = :now
                WHERE brief_id         = :brief_id
                  AND is_latest_version = TRUE
                  AND is_deleted        = FALSE
                  AND is_final          = FALSE
                """,
                {"brief_id": brief_id, "now": now.isoformat()},
            )
            self._db.commit()

        self._log_to_sheets(
            brief_id=brief_id,
            event="LOCKED",
            actor=actor,
            reason=reason,
            timestamp=now,
        )

        log.info("brief_lock.locked", brief_id=brief_id, actor=actor)

    def unlock(
        self,
        brief_id: str,
        actor: str,
        skip_approval_check: bool = False,
    ) -> None:
        """
        Setzt production_blocked = FALSE.

        Standardmäßig nur bei approval_status == 'APPROVED' erlaubt.
        skip_approval_check = True nur für interne Admin-Operationen.

        Args:
            brief_id:             Brief-Familien-ID
            actor:                Wer entsperrt
            skip_approval_check:  Erlaubt Unlock ohne APPROVED-Status

        Raises:
            BriefFinalError:       Brief ist FINAL
            UnlockNotAllowedError: approval_status != 'APPROVED'
        """
        log.info("brief_lock.unlock", brief_id=brief_id, actor=actor)

        record = self._load_and_check(brief_id)
        now    = datetime.now(timezone.utc)

        if not skip_approval_check and record.approval_status != "APPROVED":
            raise UnlockNotAllowedError(
                f"Brief {brief_id} kann nicht entsperrt werden: "
                f"approval_status='{record.approval_status}' (erwartet: 'APPROVED')"
            )

        if self._db is not None:
            self._db.execute(
                """
                UPDATE content_briefs
                SET production_blocked = FALSE,
                    updated_at         = :now
                WHERE brief_id         = :brief_id
                  AND is_latest_version = TRUE
                  AND is_deleted        = FALSE
                  AND is_final          = FALSE
                """,
                {"brief_id": brief_id, "now": now.isoformat()},
            )
            self._db.commit()

        self._log_to_sheets(
            brief_id=brief_id,
            event="UNLOCKED",
            actor=actor,
            reason=None,
            timestamp=now,
        )

        log.info("brief_lock.unlocked", brief_id=brief_id, actor=actor)

    def start_production(
        self,
        brief_id: str,
        actor: str,
    ) -> ContentBriefRecord:
        """
        Setzt production_started_at auf jetzt.
        Wird von ImageJobRunner beim Start eines Jobs aufgerufen.

        Returns:
            Aktualisierter ContentBriefRecord

        Raises:
            LockError:           Brief ist production_blocked
            BriefFinalError:     Brief ist FINAL
        """
        log.info("brief_lock.start_production", brief_id=brief_id, actor=actor)

        record = self._load_and_check(brief_id)

        if record.production_blocked:
            raise LockError(
                f"Brief {brief_id} ist production_blocked=TRUE — "
                "Produktion nicht möglich. Zuerst freigeben."
            )

        now = datetime.now(timezone.utc)

        if self._db is not None:
            self._db.execute(
                """
                UPDATE content_briefs
                SET production_started_at = :now,
                    updated_at            = :now
                WHERE brief_id            = :brief_id
                  AND is_latest_version   = TRUE
                  AND is_deleted          = FALSE
                  AND is_final            = FALSE
                """,
                {"brief_id": brief_id, "now": now.isoformat()},
            )
            self._db.commit()

            record = self._version_manager.get_latest(brief_id) or record

        self._log_to_sheets(
            brief_id=brief_id,
            event="PRODUCTION_STARTED",
            actor=actor,
            reason=None,
            timestamp=now,
        )

        log.info("brief_lock.production_started", brief_id=brief_id, actor=actor)
        return record

    def finalize(
        self,
        brief_id: str,
        actor: str,
    ) -> ContentBriefRecord:
        """
        Versiegelt den Brief unwiderruflich als FINAL.

        Bedingungen:
          - production_started_at != NULL (Produktion wurde gestartet)
          - is_final == FALSE (noch nicht final)
          - is_deleted == FALSE

        Nach finalize():
          - is_final = TRUE
          - finalized_at = jetzt
          - finalized_by = actor
          - production_blocked = FALSE (bereits in Produktion)

        Returns:
            Finalisierter ContentBriefRecord

        Raises:
            AlreadyFinalError:        Brief ist bereits FINAL
            ProductionNotStartedError: production_started_at ist NULL
        """
        log.info("brief_lock.finalize", brief_id=brief_id, actor=actor)

        record = self._load_and_check(brief_id)

        if record.is_final:
            raise AlreadyFinalError(f"Brief {brief_id} ist bereits FINAL.")

        if record.production_started_at is None:
            raise ProductionNotStartedError(
                f"Brief {brief_id} kann nicht finalisiert werden: "
                "production_started_at ist NULL. Bitte start_production() aufrufen."
            )

        now = datetime.now(timezone.utc)

        if self._db is not None:
            self._db.execute(
                """
                UPDATE content_briefs
                SET is_final           = TRUE,
                    finalized_at       = :now,
                    finalized_by       = :actor,
                    production_blocked = FALSE,
                    updated_at         = :now
                WHERE brief_id         = :brief_id
                  AND is_latest_version = TRUE
                  AND is_deleted        = FALSE
                """,
                {"brief_id": brief_id, "actor": actor, "now": now.isoformat()},
            )
            self._db.commit()

            record = self._version_manager.get_latest(brief_id) or record

        self._log_to_sheets(
            brief_id=brief_id,
            event="FINALIZED",
            actor=actor,
            reason=None,
            timestamp=now,
        )

        log.info(
            "brief_lock.finalized",
            brief_id=brief_id,
            actor=actor,
            brief_uuid=str(record.brief_uuid),
        )
        return record

    def get_lock_status(self, brief_id: str) -> dict:
        """
        Gibt aktuellen Lock-Status als Dict zurück.
        Nützlich für Health-Checks und Debug-Endpoints.
        """
        record = self._version_manager.get_latest(brief_id)
        if record is None:
            return {
                "brief_id":            brief_id,
                "found":               False,
                "production_blocked":  None,
                "is_final":            None,
                "approval_status":     None,
                "production_started_at": None,
            }
        return {
            "brief_id":              brief_id,
            "found":                 True,
            "version":               record.version,
            "production_blocked":    record.production_blocked,
            "is_final":              record.is_final,
            "approval_status":       record.approval_status,
            "validation_status":     record.validation_status,
            "production_started_at": record.production_started_at.isoformat()
            if record.production_started_at else None,
            "finalized_at":          record.finalized_at.isoformat()
            if record.finalized_at else None,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Intern
    # ─────────────────────────────────────────────────────────────────────────

    def _load_and_check(self, brief_id: str) -> ContentBriefRecord:
        """Lädt neueste Version und prüft Basis-Invarianten."""
        record = self._version_manager.get_latest(brief_id)

        if record is None:
            # Dev-Modus — Dummy zurückgeben
            from uuid import uuid4
            return ContentBriefRecord(
                brief_uuid=uuid4(),
                brief_id=brief_id,
                version=1,
                parent_version_id=None,
                rollback_source_uuid=None,
                is_latest_version=True,
                is_superseded=False,
                ab_variant=None,
                brief_json={},
                change_log=[],
                hook=None, primary_emotion=None, story_structure=None,
                scene_count=None, duration_sec=None, visual_style=None,
                camera_style=None, audio_style=None, cta_style=None,
                platform_format=None, production_complexity=None, production_risk=None,
                idea_id=None, platform=None,
                validation_status="passed",
                validation_score=None, validation_notes=None,
                validation_errors=[], validation_warnings=[],
                validated_by=None, validated_at=None,
                approval_status="APPROVED",
                approved_by=None, approved_at=None, rejection_reason=None,
                revision_reason=None, production_blocked=False,
                production_started_at=None, is_final=False, finalized_at=None,
                finalized_by=None, archived=False, archived_at=None,
                archived_reason=None, is_deleted=False, deleted_at=None,
                created_by_agent="system", schema_version="1.0.0",
                compliance_note=None, notes=None,
                created_at=datetime.now(timezone.utc), updated_at=None,
            )

        if record.is_final:
            raise BriefFinalError(
                f"Brief {brief_id} ist FINAL — keine Lock-Operationen möglich."
            )

        if record.is_deleted:
            raise BriefNotFoundError(f"Brief {brief_id} ist gelöscht.")

        return record

    def _log_to_sheets(
        self,
        brief_id: str,
        event: str,
        actor: str,
        reason: Optional[str],
        timestamp: datetime,
    ) -> None:
        if self._sheets_client is None:
            return
        try:
            self._sheets_client.append_row(
                tab=self.SHEETS_TAB,
                row={
                    "brief_id":  brief_id,
                    "event":     event,
                    "actor":     actor,
                    "reason":    reason or "",
                    "timestamp": timestamp.isoformat(),
                },
            )
        except Exception as exc:
            log.warning(
                "brief_lock.sheets.failed",
                brief_id=brief_id,
                error=str(exc),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Singleton-Accessor
# ─────────────────────────────────────────────────────────────────────────────

_lock_service: Optional[BriefLockService] = None


def get_brief_lock_service(
    db=None,
    version_manager=None,
    sheets_client=None,
) -> BriefLockService:
    global _lock_service
    if db is not None or version_manager is not None:
        return BriefLockService(
            db=db,
            version_manager=version_manager,
            sheets_client=sheets_client,
        )
    if _lock_service is None:
        _lock_service = BriefLockService()
    return _lock_service
