"""
Service: BriefVersionManager

Einziger Einstiegspunkt für alle Versions-Operationen auf content_briefs.
Kein anderer Service schreibt direkt in content_briefs.

Ablauf create_version():
  1. Aktuelle (latest) Version laden + FOR UPDATE locken
  2. Vorgänger-Check: FINAL? → Abbruch
  3. Neue Version INSERT
  4. Vorgänger UPDATE (is_latest=FALSE, is_superseded=TRUE)
  5. Change-Log INSERT in content_brief_change_log
  6. ContentBriefRecord zurückgeben

Garantien (aus content-brief-versioning.md §11):
  - Niemals zwei is_latest_version=TRUE für dieselbe brief_id (außer A/B)
  - parent_version_id verweist auf brief_uuid (nie auf brief_id)
  - version ist immer höher als parent.version
  - FINAL-Versionen können nicht als Parent einer neuen Version dienen
  - production_blocked=TRUE bei jeder neuen Version (DEFAULT)
  - Jede Versions-Erstellung schreibt in content_brief_change_log
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import text
import structlog

from app.models.content_brief import ContentBriefRecord

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class BriefVersionError(Exception):
    """Allgemeiner Fehler bei Versions-Operationen."""

class BriefNotFoundError(BriefVersionError):
    """brief_id existiert nicht in der Datenbank."""

class BriefFinalError(BriefVersionError):
    """Operation nicht erlaubt — Brief ist FINAL (bereits produziert)."""

class BriefArchivedError(BriefVersionError):
    """Operation nicht erlaubt — Brief ist archiviert."""

class BriefVersionConflictError(BriefVersionError):
    """Race Condition: eine andere Transaktion hat die Version bereits geändert."""

class InvalidTriggerCodeError(BriefVersionError):
    """Ungültiger Trigger-Code — muss TR-01 bis TR-12 sein."""


# ─────────────────────────────────────────────────────────────────────────────
# Typen
# ─────────────────────────────────────────────────────────────────────────────

VALID_TRIGGER_CODES = {
    "TR-01", "TR-02", "TR-03", "TR-04", "TR-05",
    "TR-06", "TR-07", "TR-08", "TR-09", "TR-10",
    "TR-11", "TR-12",
}

ARCHIVE_REASON_CODES = {
    "SUPERSEDED_BY_BETTER",
    "VALIDATION_NEVER_PASSED",
    "IDEA_CANCELLED",
    "PLATFORM_DISCONTINUED",
    "COMPLIANCE_HOLD",
    "OPERATOR_DECISION",
    "ROLLBACK_SOURCE",
}


@dataclass
class VersionCreationRequest:
    """Input für create_version()."""
    brief_id:        str
    new_brief_json:  dict
    revision_reason: str
    trigger_code:    str             # TR-01 bis TR-12
    actor:           str             # Agent-Name oder Operator-Username
    ab_variant:      Optional[str]   = None
    rollback_source_uuid: Optional[UUID] = None
    compliance_note: Optional[str]   = None
    schema_version:  str             = "1.0.0"


# ─────────────────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────────────────

class BriefVersionManager:
    """
    Einziger Einstiegspunkt für alle Versions-Operationen auf content_briefs.
    Alle anderen Services rufen ausschließlich diese Klasse auf,
    nie direkt die DB-Tabelle.
    """

    SHEETS_TAB_VERSIONS = "CONTENT_BRIEFS_VERSIONS"

    def __init__(self, db=None, sheets_client=None):
        self._db             = db
        self._sheets_client  = sheets_client

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche API
    # ─────────────────────────────────────────────────────────────────────────

    def create_version(self, req: VersionCreationRequest) -> ContentBriefRecord:
        """
        Atomare Erstellung einer neuen Brief-Version.

        Args:
            req: VersionCreationRequest

        Returns:
            ContentBriefRecord der neuen Version

        Raises:
            BriefNotFoundError:         brief_id existiert nicht
            BriefFinalError:            aktuelle Version ist FINAL
            BriefVersionConflictError:  Race Condition beim Locking
            InvalidTriggerCodeError:    ungültiger Trigger-Code
            ValueError:                 revision_reason leer
        """
        self._validate_request(req)

        log.info(
            "brief_version.create.start",
            brief_id=req.brief_id,
            trigger=req.trigger_code,
            actor=req.actor,
        )

        # Test-Modus ohne DB
        if self._db is None:
            return self._create_version_no_db(req)

        # Produktions-Pfad: atomare Transaktion
        try:
            # Schritt 1: Aktuelle Version laden + locken
            current = self._load_latest_for_update(req.brief_id)

            # Schritt 2: FINAL-Check
            if current["is_final"]:
                raise BriefFinalError(
                    f"Brief {req.brief_id} v{current['version']} ist FINAL — "
                    "keine neue Version möglich."
                )

            # Schritt 3: Neue Version INSERT
            new_uuid    = uuid4()
            new_version = current["version"] + 1
            now         = datetime.now(timezone.utc)

            denorm = self._extract_denormalized_fields(req.new_brief_json)
            diff   = self._compute_diff(
                current.get("brief_json", {}), req.new_brief_json
            )
            change_log_entry = self._build_change_log_entry(
                version=new_version,
                trigger_code=req.trigger_code,
                revision_reason=req.revision_reason,
                actor=req.actor,
                diff=diff,
                timestamp=now,
            )

            self._db.execute(
                text(self._INSERT_SQL),
                {
                    "brief_uuid":             str(new_uuid),
                    "brief_id":               req.brief_id,
                    "version":                new_version,
                    "parent_version_id":      str(current["brief_uuid"]),
                    "rollback_source_uuid":   str(req.rollback_source_uuid) if req.rollback_source_uuid else None,
                    "is_latest_version":      True,
                    "is_superseded":          False,
                    "ab_variant":             req.ab_variant,
                    "brief_json":             json.dumps(req.new_brief_json, ensure_ascii=False),
                    "change_log":             json.dumps([change_log_entry], ensure_ascii=False),
                    "idea_id":                current.get("idea_id"),
                    "platform":               denorm.get("platform") or current.get("platform"),
                    "hook":                   denorm.get("hook"),
                    "primary_emotion":        denorm.get("primary_emotion"),
                    "story_structure":        denorm.get("story_structure"),
                    "scene_count":            denorm.get("scene_count"),
                    "duration_sec":           denorm.get("duration_sec"),
                    "visual_style":           denorm.get("visual_style"),
                    "camera_style":           denorm.get("camera_style"),
                    "audio_style":            denorm.get("audio_style"),
                    "cta_style":              denorm.get("cta_style"),
                    "platform_format":        json.dumps(denorm.get("platform_format")) if denorm.get("platform_format") else None,
                    "production_complexity":  denorm.get("production_complexity"),
                    "production_risk":        denorm.get("production_risk"),
                    "validation_status":      "pending",
                    "approval_status":        "PENDING_VALIDATION",
                    "production_blocked":     True,
                    "revision_reason":        req.revision_reason,
                    "compliance_note":        req.compliance_note,
                    "schema_version":         req.schema_version,
                    "created_by_agent":       req.actor,
                    "created_at":             now.isoformat(),
                }
            )

            # Schritt 4: Vorgänger zurückstufen
            self._db.execute(
                text("""
                UPDATE content_briefs
                SET is_latest_version = FALSE,
                    is_superseded     = TRUE,
                    approval_status   = CASE
                        WHEN approval_status = 'APPROVED' THEN 'SUPERSEDED'
                        ELSE approval_status
                    END,
                    updated_at = NOW()
                WHERE brief_uuid = :parent_uuid
                """),
                {"parent_uuid": str(current["brief_uuid"])},
            )

            # Schritt 5: Change-Log-Tabelle
            self._insert_change_log(
                brief_id=req.brief_id,
                from_version=current["version"],
                to_version=new_version,
                from_uuid=current["brief_uuid"],
                to_uuid=new_uuid,
                event_type="REVISION" if new_version > 1 else "BRIEF_CREATED",
                trigger_code=req.trigger_code,
                trigger_reason=req.revision_reason,
                actor=req.actor,
                diff_json=diff,
                rollback_source_uuid=req.rollback_source_uuid,
                created_at=now,
            )

            # Schritt 6: Google Sheets
            self._log_to_sheets(
                brief_id=req.brief_id,
                version=new_version,
                brief_uuid=new_uuid,
                event="REVISION" if new_version > 1 else "BRIEF_CREATED",
                trigger=req.trigger_code,
                trigger_reason=req.revision_reason,
                actor=req.actor,
                platform=denorm.get("platform") or current.get("platform", "?"),
                diff=diff,
                timestamp=now,
            )

            self._db.commit()

            log.info(
                "brief_version.create.complete",
                brief_id=req.brief_id,
                version=new_version,
                brief_uuid=str(new_uuid),
            )

            return self._load_record(str(new_uuid))

        except (BriefVersionError, ValueError):
            raise
        except Exception as exc:
            log.error(
                "brief_version.create.failed",
                brief_id=req.brief_id,
                error=str(exc),
            )
            raise BriefVersionError(f"Versions-Erstellung fehlgeschlagen: {exc}") from exc

    def create_initial_version(
        self,
        brief_id: str,
        brief_json: dict,
        actor: str,
        idea_id: Optional[str] = None,
        schema_version: str = "1.0.0",
    ) -> ContentBriefRecord:
        """
        Erstellt die erste Version (v1) eines neuen Briefs.
        Kein parent_version_id — kein revision_reason erforderlich.
        """
        log.info("brief_version.initial.start", brief_id=brief_id, actor=actor)

        if self._db is None:
            return self._create_initial_no_db(
                brief_id, brief_json, actor, idea_id, schema_version
            )

        new_uuid = uuid4()
        now      = datetime.now(timezone.utc)
        denorm   = self._extract_denormalized_fields(brief_json)
        change_log_entry = self._build_change_log_entry(
            version=1,
            trigger_code=None,
            revision_reason=None,
            actor=actor,
            diff=None,
            timestamp=now,
            event="BRIEF_CREATED",
        )

        try:
            self._db.execute(
                text(self._INSERT_SQL),
                {
                    "brief_uuid":             str(new_uuid),
                    "brief_id":               brief_id,
                    "version":                1,
                    "parent_version_id":      None,
                    "rollback_source_uuid":   None,
                    "is_latest_version":      True,
                    "is_superseded":          False,
                    "ab_variant":             None,
                    "brief_json":             json.dumps(brief_json, ensure_ascii=False),
                    "change_log":             json.dumps([change_log_entry], ensure_ascii=False),
                    "idea_id":                idea_id,
                    "platform":               denorm.get("platform"),
                    "hook":                   denorm.get("hook"),
                    "primary_emotion":        denorm.get("primary_emotion"),
                    "story_structure":        denorm.get("story_structure"),
                    "scene_count":            denorm.get("scene_count"),
                    "duration_sec":           denorm.get("duration_sec"),
                    "visual_style":           denorm.get("visual_style"),
                    "camera_style":           denorm.get("camera_style"),
                    "audio_style":            denorm.get("audio_style"),
                    "cta_style":              denorm.get("cta_style"),
                    "platform_format":        json.dumps(denorm.get("platform_format")) if denorm.get("platform_format") else None,
                    "production_complexity":  denorm.get("production_complexity"),
                    "production_risk":        denorm.get("production_risk"),
                    "validation_status":      "pending",
                    "approval_status":        "PENDING_VALIDATION",
                    "production_blocked":     True,
                    "revision_reason":        None,
                    "compliance_note":        None,
                    "schema_version":         schema_version,
                    "created_by_agent":       actor,
                    "created_at":             now.isoformat(),
                }
            )

            self._insert_change_log(
                brief_id=brief_id,
                from_version=None,
                to_version=1,
                from_uuid=None,
                to_uuid=new_uuid,
                event_type="BRIEF_CREATED",
                trigger_code=None,
                trigger_reason=None,
                actor=actor,
                diff_json=None,
                rollback_source_uuid=None,
                created_at=now,
            )

            self._db.commit()

        except Exception as exc:
            self._db.rollback()
            log.error("brief_version.initial.failed", brief_id=brief_id, error=str(exc))
            raise

        log.info("brief_version.initial.complete", brief_id=brief_id, brief_uuid=str(new_uuid))
        return self._load_record(str(new_uuid))

    def rollback(
        self,
        brief_id: str,
        target_uuid: UUID,
        actor: str,
    ) -> ContentBriefRecord:
        """
        Erstellt neue Version mit brief_json der Zielversion.
        Nutzt intern create_version() mit TR-09.

        Raises:
            BriefVersionError:  Zielversion nicht in derselben brief_id-Familie
            BriefFinalError:    Zielversion ist FINAL (Rollback auf FINAL verboten)
        """
        log.info("brief_version.rollback.start", brief_id=brief_id, target=str(target_uuid))

        if self._db is None:
            raise BriefVersionError("Rollback erfordert DB-Verbindung.")

        # Zielversion laden
        target_row = self._db.execute(
            text("""
            SELECT brief_uuid, brief_id, brief_json, version, is_final, is_deleted
            FROM content_briefs
            WHERE brief_uuid = :uuid
            """),
            {"uuid": str(target_uuid)},
        ).fetchone()

        if target_row is None:
            raise BriefNotFoundError(f"Rollback-Ziel {target_uuid} nicht gefunden.")

        target_row = dict(target_row)

        if target_row["brief_id"] != brief_id:
            raise BriefVersionError(
                f"Cross-Brief-Rollback nicht erlaubt: "
                f"Ziel gehört zu '{target_row['brief_id']}', nicht zu '{brief_id}'."
            )

        if target_row["is_final"]:
            raise BriefFinalError(
                f"Rollback auf FINAL-Version {target_uuid} nicht erlaubt."
            )

        if target_row["is_deleted"]:
            raise BriefVersionError(
                f"Rollback auf gelöschte Version {target_uuid} nicht erlaubt."
            )

        req = VersionCreationRequest(
            brief_id=brief_id,
            new_brief_json=target_row["brief_json"],
            revision_reason=(
                f"Rollback zu Version {target_row['version']} "
                f"(uuid: {target_uuid})"
            ),
            trigger_code="TR-09",
            actor=actor,
            rollback_source_uuid=target_uuid,
        )

        record = self.create_version(req)
        log.info(
            "brief_version.rollback.complete",
            brief_id=brief_id,
            new_version=record.version,
            source_version=target_row["version"],
        )
        return record

    def archive(self, brief_uuid: UUID, reason: str, actor: str) -> None:
        """
        Archiviert eine Version.

        Raises:
            BriefFinalError:    is_final = TRUE
            BriefNotFoundError: brief_uuid nicht gefunden
            ValueError:         ungültiger Archivierungsgrund
        """
        if reason not in ARCHIVE_REASON_CODES:
            raise ValueError(
                f"Ungültiger archive-Reason-Code '{reason}'. "
                f"Erlaubt: {ARCHIVE_REASON_CODES}"
            )

        if self._db is None:
            raise BriefVersionError("Archive erfordert DB-Verbindung.")

        row = self._db.execute(
            text("SELECT brief_uuid, brief_id, version, is_final FROM content_briefs WHERE brief_uuid = :uuid"),
            {"uuid": str(brief_uuid)},
        ).fetchone()

        if row is None:
            raise BriefNotFoundError(f"Brief {brief_uuid} nicht gefunden.")

        row = dict(row._mapping)

        if row["is_final"]:
            raise BriefFinalError(
                f"Brief {brief_uuid} ist FINAL — Archivierung nicht möglich."
            )

        now = datetime.now(timezone.utc)

        self._db.execute(
            text("""
            UPDATE content_briefs
            SET archived           = TRUE,
                archived_at        = :now,
                archived_reason    = :reason,
                production_blocked = TRUE,
                approval_status    = CASE
                    WHEN approval_status = 'APPROVED' THEN 'SUPERSEDED'
                    ELSE approval_status
                END,
                updated_at = :now
            WHERE brief_uuid = :uuid
              AND is_final   = FALSE
            """),
            {"uuid": str(brief_uuid), "reason": reason, "now": now.isoformat()},
        )
        self._db.commit()

        self._insert_change_log(
            brief_id=row["brief_id"],
            from_version=row["version"],
            to_version=row["version"],
            from_uuid=brief_uuid,
            to_uuid=brief_uuid,
            event_type="ARCHIVED",
            trigger_code=None,
            trigger_reason=reason,
            actor=actor,
            diff_json=None,
            rollback_source_uuid=None,
            created_at=now,
        )
        self._db.commit()

        log.info(
            "brief_version.archived",
            brief_uuid=str(brief_uuid),
            reason=reason,
            actor=actor,
        )

    def get_version_chain(self, brief_id: str) -> list[ContentBriefRecord]:
        """
        Gibt alle Versionen einer brief_id zurück, älteste zuerst.
        Inkl. gelöschter und archivierter Versionen (für Audit).
        """
        if self._db is None:
            return []

        rows = self._db.execute(
            text("""
            SELECT * FROM content_briefs
            WHERE brief_id = :brief_id
            ORDER BY version ASC
            """),
            {"brief_id": brief_id},
        ).fetchall()

        if not rows:
            raise BriefNotFoundError(f"Keine Versionen für brief_id '{brief_id}' gefunden.")

        return [ContentBriefRecord.model_validate(dict(r)) for r in rows]

    def get_latest(self, brief_id: str) -> Optional[ContentBriefRecord]:
        """
        Gibt die neueste nicht-gelöschte Version zurück.
        None wenn keine Version gefunden.
        """
        if self._db is None:
            return None

        row = self._db.execute(
            text("""
            SELECT * FROM content_briefs
            WHERE brief_id         = :brief_id
              AND is_latest_version = TRUE
              AND is_deleted        = FALSE
            LIMIT 1
            """),
            {"brief_id": brief_id},
        ).fetchone()

        if row is None:
            return None

        return ContentBriefRecord.model_validate(dict(row._mapping))

    def get_by_uuid(self, brief_uuid: UUID) -> ContentBriefRecord:
        """Lädt eine spezifische Version per UUID."""
        if self._db is None:
            raise BriefVersionError("get_by_uuid erfordert DB-Verbindung.")

        row = self._db.execute(
            text("SELECT * FROM content_briefs WHERE brief_uuid = :uuid"),
            {"uuid": str(brief_uuid)},
        ).fetchone()

        if row is None:
            raise BriefNotFoundError(f"Brief {brief_uuid} nicht gefunden.")

        return ContentBriefRecord.model_validate(dict(row._mapping))

    # ─────────────────────────────────────────────────────────────────────────
    # Diff + Extraktion
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_diff(self, old_json: dict, new_json: dict) -> dict:
        """
        Feld-Level-Diff zwischen zwei brief_json-Dicts.
        Gibt nur geänderte Felder zurück — mit old/new-Wert.
        Arrays: bei Längenänderung oder Inhalt-Diff als Block gelistet.
        JSONB-Blöcke: Rekursiv bis zur Blatt-Ebene verglichen.
        """
        diff: dict[str, Any] = {}
        self._flatten_diff(old_json, new_json, prefix="", diff=diff)
        return diff

    def _flatten_diff(
        self,
        old: Any,
        new: Any,
        prefix: str,
        diff: dict,
    ) -> None:
        if isinstance(old, dict) and isinstance(new, dict):
            all_keys = set(old.keys()) | set(new.keys())
            for key in sorted(all_keys):
                self._flatten_diff(
                    old.get(key),
                    new.get(key),
                    f"{prefix}.{key}" if prefix else key,
                    diff,
                )
        elif isinstance(old, list) and isinstance(new, list):
            if old != new:
                diff[prefix] = {"old": old, "new": new}
        else:
            if old != new:
                diff[prefix] = {"old": old, "new": new}

    def _extract_denormalized_fields(self, brief_json: dict) -> dict:
        """
        Extrahiert alle Schnellzugriff-Felder aus brief_json.
        Quelle: content-brief-versioning.md §2.2
        """
        brief = brief_json.get("brief", {})

        # Plattform-Format als dict
        pf = brief.get("platform_format")
        if not isinstance(pf, dict):
            pf = None

        # Szenen-Anzahl
        scenes     = brief.get("scene_structure") or []
        scene_count = len(scenes) if isinstance(scenes, list) else None

        # Produktions-Scores
        prod_cx  = None
        prod_risk = None
        if isinstance(brief.get("production_complexity"), dict):
            prod_cx = brief["production_complexity"].get("score")
        if isinstance(brief.get("production_risk"), dict):
            prod_risk = brief["production_risk"].get("score")

        return {
            "platform":            brief.get("platform"),
            "hook":                brief.get("hook", {}).get("text") if isinstance(brief.get("hook"), dict) else brief.get("hook"),
            "primary_emotion":     brief.get("main_emotion"),
            "story_structure":     (brief.get("story_structure") or {}).get("type") if isinstance(brief.get("story_structure"), dict) else None,
            "scene_count":         scene_count,
            "duration_sec":        (brief.get("video_length") or {}).get("target_sec") if isinstance(brief.get("video_length"), dict) else None,
            "visual_style":        (brief.get("visual_mood") or {}).get("texture_feel") if isinstance(brief.get("visual_mood"), dict) else None,
            "camera_style":        (brief.get("camera_style") or {}).get("primary") if isinstance(brief.get("camera_style"), dict) else None,
            "audio_style":         (brief.get("audio_mood") or {}).get("type") if isinstance(brief.get("audio_mood"), dict) else None,
            "cta_style":           (brief.get("cta_style") or {}).get("type") if isinstance(brief.get("cta_style"), dict) else None,
            "platform_format":     pf,
            "production_complexity": prod_cx,
            "production_risk":     prod_risk,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # DB-Hilfsmethoden
    # ─────────────────────────────────────────────────────────────────────────

    def _load_latest_for_update(self, brief_id: str) -> dict:
        """Lädt aktuelle Version mit SELECT ... FOR UPDATE."""
        row = self._db.execute(
            text("""
            SELECT *
            FROM content_briefs
            WHERE brief_id         = :brief_id
              AND is_latest_version = TRUE
              AND is_deleted        = FALSE
            FOR UPDATE
            """),
            {"brief_id": brief_id},
        ).fetchone()

        if row is None:
            raise BriefNotFoundError(
                f"Brief '{brief_id}' nicht gefunden oder bereits gelöscht."
            )
        return dict(row._mapping)

    def _load_record(self, brief_uuid_str: str) -> ContentBriefRecord:
        row = self._db.execute(
            text("SELECT * FROM content_briefs WHERE brief_uuid = :uuid"),
            {"uuid": brief_uuid_str},
        ).fetchone()
        if row is None:
            raise BriefNotFoundError(f"Neu erstellte Version {brief_uuid_str} nicht gefunden.")
        return ContentBriefRecord.model_validate(dict(row._mapping))

    def _insert_change_log(
        self,
        brief_id: str,
        from_version: Optional[int],
        to_version: int,
        from_uuid: Optional[UUID],
        to_uuid: UUID,
        event_type: str,
        trigger_code: Optional[str],
        trigger_reason: Optional[str],
        actor: str,
        diff_json: Optional[dict],
        rollback_source_uuid: Optional[UUID],
        created_at: datetime,
    ) -> None:
        if self._db is None:
            return
        self._db.execute(
            text("""
            INSERT INTO content_brief_change_log (
                log_uuid, brief_id, from_version, to_version,
                from_uuid, to_uuid, event_type, trigger_code,
                trigger_reason, actor, diff_json,
                rollback_source_uuid, created_at
            ) VALUES (
                :log_uuid, :brief_id, :from_version, :to_version,
                :from_uuid, :to_uuid, :event_type, :trigger_code,
                :trigger_reason, :actor, CAST(:diff_json AS jsonb),
                :rollback_source_uuid, :created_at
            )
            """),
            {
                "log_uuid":             str(uuid4()),
                "brief_id":             brief_id,
                "from_version":         from_version,
                "to_version":           to_version,
                "from_uuid":            str(from_uuid) if from_uuid else None,
                "to_uuid":              str(to_uuid),
                "event_type":           event_type,
                "trigger_code":         trigger_code,
                "trigger_reason":       trigger_reason,
                "actor":                actor,
                "diff_json":            json.dumps(diff_json, ensure_ascii=False) if diff_json else None,
                "rollback_source_uuid": str(rollback_source_uuid) if rollback_source_uuid else None,
                "created_at":           created_at.isoformat(),
            }
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Google Sheets
    # ─────────────────────────────────────────────────────────────────────────

    def _log_to_sheets(
        self,
        brief_id: str,
        version: int,
        brief_uuid: UUID,
        event: str,
        trigger: Optional[str],
        trigger_reason: Optional[str],
        actor: str,
        platform: str,
        diff: dict,
        timestamp: datetime,
    ) -> None:
        if self._sheets_client is None:
            return
        try:
            diff_summary = ", ".join(
                k for k in list(diff.keys())[:5]
            ) if diff else ""

            self._sheets_client.append_row(
                tab=self.SHEETS_TAB_VERSIONS,
                row={
                    "brief_id":       brief_id,
                    "version":        version,
                    "brief_uuid":     str(brief_uuid),
                    "event":          event,
                    "trigger":        trigger or "",
                    "trigger_reason": trigger_reason or "",
                    "actor":          actor,
                    "platform":       platform,
                    "timestamp":      timestamp.isoformat(),
                    "diff_summary":   diff_summary,
                },
            )
        except Exception as exc:
            log.warning(
                "brief_version.sheets.failed",
                brief_id=brief_id,
                error=str(exc),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Hilfsmethoden
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_request(req: VersionCreationRequest) -> None:
        if req.trigger_code not in VALID_TRIGGER_CODES:
            raise InvalidTriggerCodeError(
                f"Ungültiger Trigger-Code '{req.trigger_code}'. "
                f"Erlaubt: TR-01 bis TR-12."
            )
        if not req.revision_reason or not req.revision_reason.strip():
            raise ValueError("revision_reason darf nicht leer sein.")
        if not req.actor or not req.actor.strip():
            raise ValueError("actor darf nicht leer sein.")
        if not req.brief_id or not req.brief_id.strip():
            raise ValueError("brief_id darf nicht leer sein.")

    @staticmethod
    def _build_change_log_entry(
        version: int,
        trigger_code: Optional[str],
        revision_reason: Optional[str],
        actor: str,
        diff: Optional[dict],
        timestamp: datetime,
        event: str = "REVISION",
    ) -> dict:
        entry: dict[str, Any] = {
            "version":   version,
            "event":     event if version == 1 else "REVISION",
            "timestamp": timestamp.isoformat(),
            "actor":     actor,
        }
        if trigger_code:
            entry["trigger"]        = trigger_code
            entry["trigger_reason"] = revision_reason
        if diff:
            entry["diff"] = diff
        return entry

    # ─────────────────────────────────────────────────────────────────────────
    # Test-Modus (kein DB-Zugriff)
    # ─────────────────────────────────────────────────────────────────────────

    def _create_version_no_db(self, req: VersionCreationRequest) -> ContentBriefRecord:
        """Gibt einen Dummy-Record zurück wenn kein DB-Client vorhanden."""
        now    = datetime.now(timezone.utc)
        denorm = self._extract_denormalized_fields(req.new_brief_json)
        return ContentBriefRecord(
            brief_uuid=uuid4(),
            brief_id=req.brief_id,
            version=2,
            parent_version_id=uuid4(),
            rollback_source_uuid=req.rollback_source_uuid,
            is_latest_version=True,
            is_superseded=False,
            ab_variant=req.ab_variant,
            brief_json=req.new_brief_json,
            change_log=[],
            hook=denorm.get("hook"),
            primary_emotion=denorm.get("primary_emotion"),
            story_structure=denorm.get("story_structure"),
            scene_count=denorm.get("scene_count"),
            duration_sec=denorm.get("duration_sec"),
            visual_style=denorm.get("visual_style"),
            camera_style=denorm.get("camera_style"),
            audio_style=denorm.get("audio_style"),
            cta_style=denorm.get("cta_style"),
            platform_format=denorm.get("platform_format"),
            production_complexity=denorm.get("production_complexity"),
            production_risk=denorm.get("production_risk"),
            idea_id=None,
            platform=denorm.get("platform"),
            validation_status="pending",
            validation_score=None,
            validation_notes=None,
            validation_errors=[],
            validation_warnings=[],
            validated_by=None,
            validated_at=None,
            approval_status="PENDING_VALIDATION",
            approved_by=None,
            approved_at=None,
            rejection_reason=None,
            revision_reason=req.revision_reason,
            production_blocked=True,
            production_started_at=None,
            is_final=False,
            finalized_at=None,
            finalized_by=None,
            archived=False,
            archived_at=None,
            archived_reason=None,
            is_deleted=False,
            deleted_at=None,
            created_by_agent=req.actor,
            schema_version=req.schema_version,
            compliance_note=req.compliance_note,
            notes=None,
            created_at=now,
            updated_at=None,
        )

    def _create_initial_no_db(
        self,
        brief_id: str,
        brief_json: dict,
        actor: str,
        idea_id: Optional[str],
        schema_version: str,
    ) -> ContentBriefRecord:
        now    = datetime.now(timezone.utc)
        denorm = self._extract_denormalized_fields(brief_json)
        return ContentBriefRecord(
            brief_uuid=uuid4(),
            brief_id=brief_id,
            version=1,
            parent_version_id=None,
            rollback_source_uuid=None,
            is_latest_version=True,
            is_superseded=False,
            ab_variant=None,
            brief_json=brief_json,
            change_log=[],
            hook=denorm.get("hook"),
            primary_emotion=denorm.get("primary_emotion"),
            story_structure=denorm.get("story_structure"),
            scene_count=denorm.get("scene_count"),
            duration_sec=denorm.get("duration_sec"),
            visual_style=denorm.get("visual_style"),
            camera_style=denorm.get("camera_style"),
            audio_style=denorm.get("audio_style"),
            cta_style=denorm.get("cta_style"),
            platform_format=denorm.get("platform_format"),
            production_complexity=denorm.get("production_complexity"),
            production_risk=denorm.get("production_risk"),
            idea_id=idea_id,
            platform=denorm.get("platform"),
            validation_status="pending",
            validation_score=None,
            validation_notes=None,
            validation_errors=[],
            validation_warnings=[],
            validated_by=None,
            validated_at=None,
            approval_status="PENDING_VALIDATION",
            approved_by=None,
            approved_at=None,
            rejection_reason=None,
            revision_reason=None,
            production_blocked=True,
            production_started_at=None,
            is_final=False,
            finalized_at=None,
            finalized_by=None,
            archived=False,
            archived_at=None,
            archived_reason=None,
            is_deleted=False,
            deleted_at=None,
            created_by_agent=actor,
            schema_version=schema_version,
            compliance_note=None,
            notes=None,
            created_at=now,
            updated_at=None,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # SQL-Konstanten
    # ─────────────────────────────────────────────────────────────────────────

    _INSERT_SQL = """
        INSERT INTO content_briefs (
            brief_uuid, brief_id, version,
            parent_version_id, rollback_source_uuid,
            is_latest_version, is_superseded, ab_variant,
            brief_json, change_log,
            idea_id, platform,
            hook, primary_emotion, story_structure, scene_count,
            duration_sec, visual_style, camera_style, audio_style,
            cta_style, platform_format,
            production_complexity, production_risk,
            validation_status, approval_status, production_blocked,
            revision_reason, compliance_note, schema_version,
            created_by_agent, created_at
        ) VALUES (
            :brief_uuid, :brief_id, :version,
            :parent_version_id, :rollback_source_uuid,
            :is_latest_version, :is_superseded, :ab_variant,
            CAST(:brief_json AS jsonb), CAST(:change_log AS jsonb),
            :idea_id, :platform,
            :hook, :primary_emotion, :story_structure, :scene_count,
            :duration_sec, :visual_style, :camera_style, :audio_style,
            :cta_style, CAST(:platform_format AS jsonb),
            :production_complexity, :production_risk,
            :validation_status, :approval_status, :production_blocked,
            :revision_reason, :compliance_note, :schema_version,
            :created_by_agent, :created_at
        )
    """
