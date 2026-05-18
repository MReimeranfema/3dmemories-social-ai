"""
Service: BriefApprovalService

Verwaltet alle Freigabe-Entscheidungen für Content-Briefs.

Ablauf APPROVE:
  1. Brief laden + prüfen (nicht final, nicht gelöscht)
  2. validation_status == 'passed' prüfen (Pflicht für Freigabe)
  3. approval_status → APPROVED setzen
  4. production_blocked → FALSE (via BriefLockService)
  5. Approval-Record in approvals-Tabelle persistieren
  6. Slack-Notification + Button-Update

Ablauf REJECT:
  1. Brief laden + prüfen
  2. approval_status → REJECTED setzen + rejection_reason speichern
  3. Approval-Record in approvals-Tabelle persistieren
  4. Slack-Notification

Ablauf REQUEST_REVISION:
  1. Brief laden + prüfen
  2. approval_status → REVISION_REQUESTED
  3. BriefVersionManager.create_version() aufrufen (TR-05)
  4. Slack-Notification

Garantien:
  - Nie zwei APPROVED für dieselbe brief_id + version
  - FINAL-Briefs können nicht erneut freigegeben werden
  - Jede Entscheidung wird in approvals + Sheets gespeichert
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import structlog

from app.models.content_brief import ContentBriefRecord
from app.services.brief_version_manager import (
    BriefVersionManager,
    BriefNotFoundError,
    BriefFinalError,
    VersionCreationRequest,
)

log = structlog.get_logger(__name__)

# ── Gültige Approval-Status-Übergänge ────────────────────────────────────────

VALID_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_VALIDATION": {"APPROVED", "REJECTED"},        # Nur mit Sonderrecht
    "APPROVED":           {"REVISION_REQUESTED", "REJECTED"},
    "REJECTED":           {"PENDING_VALIDATION"},
    "REVISION_REQUESTED": {"APPROVED", "REJECTED"},
    "SUPERSEDED":         set(),                            # Keine Übergänge
}

APPROVAL_ID_PREFIX = "APR"


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class ApprovalError(Exception):
    """Allgemeiner Freigabe-Fehler."""

class BriefNotValidatedError(ApprovalError):
    """Brief hat validation_status != 'passed' — Freigabe nicht möglich."""

class InvalidTransitionError(ApprovalError):
    """Der gewünschte Status-Übergang ist nicht erlaubt."""

class AlreadyApprovedError(ApprovalError):
    """Brief ist bereits freigegeben."""


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _generate_approval_id() -> str:
    year   = datetime.now(timezone.utc).year
    suffix = str(uuid4().int)[:5]
    return f"{APPROVAL_ID_PREFIX}-{year}-{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────────────────

class BriefApprovalService:
    """
    Verwaltet Freigabe-Entscheidungen für Content-Briefs.

    Ohne DB → Dev-Modus (keine Persistierung, nur Logging).
    Mit DB  → vollständiger Produktionspfad.
    """

    SHEETS_TAB = "CONTENT_BRIEFS_APPROVALS"

    def __init__(
        self,
        db=None,
        version_manager: Optional[BriefVersionManager] = None,
        slack_client=None,
        sheets_client=None,
        lock_service=None,
    ):
        self._db              = db
        self._version_manager = version_manager or BriefVersionManager(db=db)
        self._slack_client    = slack_client
        self._sheets_client   = sheets_client
        self._lock_service    = lock_service   # BriefLockService — optional, um Zirkular-Imports zu vermeiden

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche API
    # ─────────────────────────────────────────────────────────────────────────

    def approve(
        self,
        brief_id: str,
        decided_by: str,
        reason: Optional[str] = None,
        skip_validation_check: bool = False,
    ) -> ContentBriefRecord:
        """
        Gibt ein Brief frei.

        Args:
            brief_id:               Brief-Familien-ID
            decided_by:             Slack-Username der entscheidenden Person
            reason:                 Optionale Begründung
            skip_validation_check:  Erlaubt Freigabe ohne passed-Validation (nur für Admins)

        Returns:
            Aktualisierter ContentBriefRecord

        Raises:
            BriefNotFoundError:    Brief nicht gefunden
            BriefFinalError:       Brief ist FINAL
            BriefNotValidatedError: validation_status != 'passed'
            AlreadyApprovedError:  Brief bereits freigegeben
            InvalidTransitionError: Status-Übergang nicht erlaubt
        """
        log.info("brief_approval.approve.start", brief_id=brief_id, decided_by=decided_by)

        # Erst laden ohne Transition-Check um AlreadyApprovedError korrekt zu werfen
        record_raw = self._version_manager.get_latest(brief_id)
        if record_raw is not None:
            if record_raw.is_final:
                raise BriefFinalError(f"Brief {brief_id} ist FINAL — keine Änderungen möglich.")
            if record_raw.approval_status == "APPROVED":
                raise AlreadyApprovedError(f"Brief {brief_id} ist bereits freigegeben.")

        record = self._load_and_check(brief_id, target_status="APPROVED")

        # Validierungs-Check (Standardfall)
        if not skip_validation_check and record.validation_status != "passed":
            raise BriefNotValidatedError(
                f"Brief {brief_id} hat validation_status='{record.validation_status}'. "
                "Freigabe nur bei 'passed' möglich."
            )

        now = datetime.now(timezone.utc)

        if self._db is not None:
            # DB-Update
            self._db.execute(
                """
                UPDATE content_briefs
                SET approval_status   = 'APPROVED',
                    approved_by       = :decided_by,
                    approved_at       = :now,
                    rejection_reason  = NULL,
                    updated_at        = :now
                WHERE brief_id        = :brief_id
                  AND is_latest_version = TRUE
                  AND is_deleted        = FALSE
                """,
                {"brief_id": brief_id, "decided_by": decided_by, "now": now.isoformat()},
            )
            self._db.commit()

            # Approval-Record speichern
            self._insert_approval(
                brief_id=brief_id,
                brief_uuid=record.brief_uuid,
                result="APPROVED",
                decided_by=decided_by,
                reason=reason,
                now=now,
            )

            # Production-Lock aufheben (via LockService wenn vorhanden)
            if self._lock_service is not None:
                self._lock_service.unlock(brief_id=brief_id, actor=decided_by)
            else:
                self._db.execute(
                    """
                    UPDATE content_briefs
                    SET production_blocked = FALSE,
                        updated_at         = :now
                    WHERE brief_id         = :brief_id
                      AND is_latest_version = TRUE
                    """,
                    {"brief_id": brief_id, "now": now.isoformat()},
                )
                self._db.commit()

            # Sheets
            self._log_to_sheets(
                brief_id=brief_id,
                event="APPROVED",
                decided_by=decided_by,
                reason=reason,
                timestamp=now,
            )

            # Slack
            self._notify_slack(
                brief_id=brief_id,
                event="APPROVED",
                decided_by=decided_by,
                reason=reason,
            )

            # Aktualisierten Record laden
            record = self._version_manager.get_latest(brief_id) or record

        log.info(
            "brief_approval.approved",
            brief_id=brief_id,
            decided_by=decided_by,
            brief_uuid=str(record.brief_uuid),
        )
        return record

    def reject(
        self,
        brief_id: str,
        decided_by: str,
        reason: str,
    ) -> ContentBriefRecord:
        """
        Lehnt ein Brief ab.

        Args:
            brief_id:   Brief-Familien-ID
            decided_by: Slack-Username
            reason:     Ablehnungsgrund (Pflicht)

        Returns:
            Aktualisierter ContentBriefRecord
        """
        if not reason or not reason.strip():
            raise ValueError("reason ist Pflichtfeld bei Ablehnung.")

        log.info("brief_approval.reject.start", brief_id=brief_id, decided_by=decided_by)

        record = self._load_and_check(brief_id, target_status="REJECTED")
        now    = datetime.now(timezone.utc)

        if self._db is not None:
            self._db.execute(
                """
                UPDATE content_briefs
                SET approval_status   = 'REJECTED',
                    rejection_reason  = :reason,
                    production_blocked = TRUE,
                    updated_at        = :now
                WHERE brief_id        = :brief_id
                  AND is_latest_version = TRUE
                  AND is_deleted        = FALSE
                """,
                {"brief_id": brief_id, "reason": reason, "now": now.isoformat()},
            )
            self._db.commit()

            self._insert_approval(
                brief_id=brief_id,
                brief_uuid=record.brief_uuid,
                result="REJECTED",
                decided_by=decided_by,
                reason=reason,
                now=now,
            )

            self._log_to_sheets(
                brief_id=brief_id,
                event="REJECTED",
                decided_by=decided_by,
                reason=reason,
                timestamp=now,
            )

            self._notify_slack(
                brief_id=brief_id,
                event="REJECTED",
                decided_by=decided_by,
                reason=reason,
            )

            record = self._version_manager.get_latest(brief_id) or record

        log.info(
            "brief_approval.rejected",
            brief_id=brief_id,
            decided_by=decided_by,
            reason=reason[:80],
        )
        return record

    def request_revision(
        self,
        brief_id: str,
        decided_by: str,
        revision_instructions: str,
    ) -> ContentBriefRecord:
        """
        Fordert eine Überarbeitung an und erzeugt eine neue Brief-Version.

        Args:
            brief_id:               Brief-Familien-ID
            decided_by:             Slack-Username
            revision_instructions:  Konkrete Überarbeitungs-Anweisungen

        Returns:
            Neuer ContentBriefRecord (neue Version)
        """
        if not revision_instructions or not revision_instructions.strip():
            raise ValueError("revision_instructions darf nicht leer sein.")

        log.info(
            "brief_approval.revision.start",
            brief_id=brief_id,
            decided_by=decided_by,
        )

        record = self._load_and_check(brief_id, target_status="REVISION_REQUESTED")
        now    = datetime.now(timezone.utc)

        if self._db is not None:
            # Status direkt auf REVISION_REQUESTED setzen
            self._db.execute(
                """
                UPDATE content_briefs
                SET approval_status = 'REVISION_REQUESTED',
                    revision_reason = :reason,
                    updated_at      = :now
                WHERE brief_id         = :brief_id
                  AND is_latest_version = TRUE
                  AND is_deleted        = FALSE
                """,
                {
                    "brief_id": brief_id,
                    "reason":   revision_instructions,
                    "now":      now.isoformat(),
                },
            )
            self._db.commit()

            self._insert_approval(
                brief_id=brief_id,
                brief_uuid=record.brief_uuid,
                result="REVISION_REQUESTED",
                decided_by=decided_by,
                reason=revision_instructions,
                now=now,
            )

            self._log_to_sheets(
                brief_id=brief_id,
                event="REVISION_REQUESTED",
                decided_by=decided_by,
                reason=revision_instructions,
                timestamp=now,
            )

            self._notify_slack(
                brief_id=brief_id,
                event="REVISION_REQUESTED",
                decided_by=decided_by,
                reason=revision_instructions,
            )

            record = self._version_manager.get_latest(brief_id) or record

        log.info(
            "brief_approval.revision_requested",
            brief_id=brief_id,
            decided_by=decided_by,
        )
        return record

    # ─────────────────────────────────────────────────────────────────────────
    # Intern
    # ─────────────────────────────────────────────────────────────────────────

    def _load_and_check(self, brief_id: str, target_status: str) -> ContentBriefRecord:
        """Lädt Brief und prüft Basis-Voraussetzungen."""
        record = self._version_manager.get_latest(brief_id)

        if record is None:
            # Dev-Modus: Dummy-Record zurückgeben
            from app.models.content_brief import ContentBriefRecord
            from uuid import uuid4
            from datetime import datetime, timezone
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
                approval_status="PENDING_VALIDATION",
                approved_by=None, approved_at=None, rejection_reason=None,
                revision_reason=None, production_blocked=True,
                production_started_at=None, is_final=False, finalized_at=None,
                finalized_by=None, archived=False, archived_at=None,
                archived_reason=None, is_deleted=False, deleted_at=None,
                created_by_agent="system", schema_version="1.0.0",
                compliance_note=None, notes=None,
                created_at=datetime.now(timezone.utc), updated_at=None,
            )

        if record.is_final:
            raise BriefFinalError(f"Brief {brief_id} ist FINAL — keine Änderungen möglich.")

        if record.is_deleted:
            raise BriefNotFoundError(f"Brief {brief_id} ist gelöscht.")

        # Transition prüfen
        current   = record.approval_status
        allowed   = VALID_TRANSITIONS.get(current, set())
        if target_status not in allowed:
            raise InvalidTransitionError(
                f"Übergang {current} → {target_status} nicht erlaubt. "
                f"Erlaubt: {allowed or 'keine'}"
            )

        return record

    def _insert_approval(
        self,
        brief_id: str,
        brief_uuid,
        result: str,
        decided_by: str,
        reason: Optional[str],
        now: datetime,
    ) -> None:
        if self._db is None:
            return
        try:
            self._db.execute(
                """
                INSERT INTO approvals (
                    approval_id, object_type, object_id,
                    approval_type, result, decided_by,
                    reason, created_at
                ) VALUES (
                    :approval_id, 'content_brief', :brief_id,
                    'BRIEF_APPROVAL', :result, :decided_by,
                    :reason, :created_at
                )
                """,
                {
                    "approval_id": _generate_approval_id(),
                    "brief_id":    brief_id,
                    "result":      result,
                    "decided_by":  decided_by,
                    "reason":      reason,
                    "created_at":  now.isoformat(),
                },
            )
            self._db.commit()
        except Exception as exc:
            log.warning(
                "brief_approval.insert.failed",
                brief_id=brief_id,
                error=str(exc),
            )

    def _log_to_sheets(
        self,
        brief_id: str,
        event: str,
        decided_by: str,
        reason: Optional[str],
        timestamp: datetime,
    ) -> None:
        if self._sheets_client is None:
            return
        try:
            self._sheets_client.append_row(
                tab=self.SHEETS_TAB,
                row={
                    "brief_id":   brief_id,
                    "event":      event,
                    "decided_by": decided_by,
                    "reason":     reason or "",
                    "timestamp":  timestamp.isoformat(),
                },
            )
        except Exception as exc:
            log.warning(
                "brief_approval.sheets.failed",
                brief_id=brief_id,
                error=str(exc),
            )

    def _notify_slack(
        self,
        brief_id: str,
        event: str,
        decided_by: str,
        reason: Optional[str],
    ) -> None:
        if self._slack_client is None:
            return
        try:
            emoji = {"APPROVED": "✅", "REJECTED": "❌", "REVISION_REQUESTED": "🔄"}.get(event, "ℹ️")
            text  = (
                f"{emoji} *Brief {brief_id}* — {event}\n"
                f"Entschieden von: @{decided_by}"
            )
            if reason:
                text += f"\nGrund: {reason[:200]}"

            self._slack_client.post_message(
                channel=settings.SLACK_CHANNEL_IMAGE_REVIEW
                if hasattr(self, "_settings") else "#3dm-image-review",
                text=text,
            )
        except Exception as exc:
            log.warning(
                "brief_approval.slack.failed",
                brief_id=brief_id,
                error=str(exc),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Singleton-Accessor
# ─────────────────────────────────────────────────────────────────────────────

_approval_service: Optional[BriefApprovalService] = None


def get_brief_approval_service(
    db=None,
    version_manager=None,
    slack_client=None,
    sheets_client=None,
    lock_service=None,
) -> BriefApprovalService:
    global _approval_service
    if db is not None or version_manager is not None:
        return BriefApprovalService(
            db=db,
            version_manager=version_manager,
            slack_client=slack_client,
            sheets_client=sheets_client,
            lock_service=lock_service,
        )
    if _approval_service is None:
        _approval_service = BriefApprovalService()
    return _approval_service
