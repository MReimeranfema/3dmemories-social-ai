"""
Tests für BriefEngine, BriefApprovalService, BriefLockService.

Laufen vollständig ohne DB und ohne externe APIs (Dev-Modus).
Alle Services arbeiten mit Mock-Clients oder ohne Clients.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.agents.brief_engine import (
    BriefEngine,
    BriefGenerationError,
    BriefSchemaError,
    IdeaNotFoundError,
    _extract_json,
    _validate_brief_schema,
    _generate_brief_id,
)
from app.services.brief_approval_service import (
    BriefApprovalService,
    ApprovalError,
    BriefNotValidatedError,
    InvalidTransitionError,
    AlreadyApprovedError,
    get_brief_approval_service,
)
from app.services.brief_lock_service import (
    BriefLockService,
    LockError,
    UnlockNotAllowedError,
    AlreadyFinalError,
    ProductionNotStartedError,
    get_brief_lock_service,
)
from app.services.brief_version_manager import (
    BriefVersionManager,
    BriefFinalError,
    BriefNotFoundError,
)
from app.models.content_brief import ContentBriefRecord


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_record(**overrides) -> ContentBriefRecord:
    """Minimal-ContentBriefRecord für Tests."""
    defaults = dict(
        brief_uuid=uuid4(),
        brief_id="BRIEF-2026-00001",
        version=1,
        parent_version_id=None,
        rollback_source_uuid=None,
        is_latest_version=True,
        is_superseded=False,
        ab_variant=None,
        brief_json={"brief": {"platform": "instagram"}},
        change_log=[],
        hook=None, primary_emotion=None, story_structure=None,
        scene_count=None, duration_sec=None, visual_style=None,
        camera_style=None, audio_style=None, cta_style=None,
        platform_format=None, production_complexity=None, production_risk=None,
        idea_id="IDEA-2026-001", platform="instagram",
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
        created_by_agent="brief-engine", schema_version="1.0.0",
        compliance_note=None, notes=None,
        created_at=datetime.now(timezone.utc), updated_at=None,
    )
    defaults.update(overrides)
    return ContentBriefRecord(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# BriefEngine — Unit Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBriefEngineHelpers:
    """Tests für Hilfsfunktionen ohne externe Abhängigkeiten."""

    def test_generate_brief_id_format(self):
        brief_id = _generate_brief_id()
        assert brief_id.startswith("BRIEF-")
        parts = brief_id.split("-")
        assert len(parts) == 3
        assert parts[1].isdigit()
        assert len(parts[1]) == 4   # Jahr

    def test_extract_json_clean(self):
        text   = '{"platform": "instagram", "hook": "Test"}'
        result = _extract_json(text)
        assert result["platform"] == "instagram"
        assert result["hook"] == "Test"

    def test_extract_json_with_markdown(self):
        text = '```json\n{"platform": "instagram"}\n```'
        result = _extract_json(text)
        assert result["platform"] == "instagram"

    def test_extract_json_no_json_raises(self):
        with pytest.raises(BriefGenerationError):
            _extract_json("Kein JSON hier.")

    def test_validate_brief_schema_ok(self):
        brief_json = {
            "platform": "instagram",
            "hook": "Test-Hook",
            "main_emotion": "Nostalgie",
            "story_structure": {"type": "before_after"},
            "visual_mood": {"color_palette": []},
            "cta_style": {"type": "soft"},
            "caption": {"instagram": "Test"},
            "hashtags": ["#test"],
        }
        missing = _validate_brief_schema(brief_json)
        assert missing == []

    def test_validate_brief_schema_missing(self):
        missing = _validate_brief_schema({"platform": "instagram"})
        assert "hook" in missing
        assert "main_emotion" in missing
        assert len(missing) == 7   # 8 Pflichtfelder - 1 (platform vorhanden)


class TestBriefEngineDevMode:
    """BriefEngine im Dev-Modus (kein DB, kein Claude)."""

    def _make_engine_with_mock_claude(self, brief_json_response: dict) -> BriefEngine:
        """Erstellt BriefEngine mit Mock-Claude der ein valides Brief zurückgibt."""
        import json

        mock_claude  = MagicMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps(brief_json_response)
        mock_claude.call.return_value = mock_response

        engine = BriefEngine(db=None, slack_client=None)
        engine._claude = mock_claude
        return engine

    def _valid_brief_json(self) -> dict:
        return {
            "platform": "instagram",
            "format": "image_post",
            "occasion": "Schöne Erinnerung",
            "hook": {"text": "Erinnerungen, die für immer bleiben", "type": "emotion"},
            "main_emotion": "Nostalgie",
            "story_structure": {"type": "before_after", "beats": ["A", "B", "C"]},
            "scene_structure": [{"scene_id": 1, "duration_sec": 5, "description": "Test", "camera": "close-up", "audio": "music"}],
            "video_length": {"target_sec": 30, "min_sec": 15, "max_sec": 60},
            "visual_mood": {"color_palette": ["#F44D13"], "texture_feel": "warm", "lighting": "soft"},
            "camera_style": {"primary": "static", "movement": "none"},
            "audio_mood": {"type": "music", "description": "Soft piano", "tempo": "slow"},
            "cta_style": {"type": "soft", "text": "Jetzt entdecken"},
            "caption": {"instagram": "Test Caption", "facebook": "Test Caption FB"},
            "hashtags": ["#3dmemories", "#erinnerung"],
            "production_complexity": {"score": 2, "reason": "Einfach"},
            "production_risk": {"score": 1, "reason": "Kein Risiko"},
            "image_prompt": "A warm flatlay with handcrafted 3D figure",
        }

    def test_generate_returns_record_without_db(self):
        engine = self._make_engine_with_mock_claude(self._valid_brief_json())
        record = engine.generate(
            idea_id="IDEA-2026-001",
            platform="instagram",
        )
        assert record.brief_id.startswith("BRIEF-")
        assert record.version == 1
        assert record.platform == "instagram"
        assert record.is_latest_version is True

    def test_generate_sets_correct_platform(self):
        brief = self._valid_brief_json()
        engine = self._make_engine_with_mock_claude(brief)
        record = engine.generate("IDEA-2026-001", platform="facebook")
        assert record.platform == "facebook"

    def test_generate_retries_on_schema_error(self):
        import json

        valid_brief = self._valid_brief_json()
        invalid_response = MagicMock()
        invalid_response.content = '{"platform": "instagram"}'  # Fehlt viele Felder

        valid_response = MagicMock()
        valid_response.content = json.dumps(valid_brief)

        mock_claude = MagicMock()
        # 1. Aufruf: fehlerhafte Antwort → 2. Aufruf: valide Antwort
        mock_claude.call.side_effect = [invalid_response, valid_response]

        engine = BriefEngine(db=None, slack_client=None)
        engine._claude = mock_claude

        record = engine.generate("IDEA-2026-001")
        assert record is not None
        assert mock_claude.call.call_count == 2   # Retry hat funktioniert


# ─────────────────────────────────────────────────────────────────────────────
# BriefApprovalService — Unit Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBriefApprovalServiceDevMode:
    """BriefApprovalService im Dev-Modus ohne DB."""

    def _make_service(self, record: ContentBriefRecord) -> BriefApprovalService:
        mock_vm = MagicMock()
        mock_vm.get_latest.return_value = record
        return BriefApprovalService(db=None, version_manager=mock_vm)

    def test_approve_passed_brief_ok(self):
        record  = _make_record(validation_status="passed", approval_status="PENDING_VALIDATION")
        service = self._make_service(record)
        # Dev-Modus: kein DB → einfach validiert ohne Fehler
        result  = service.approve("BRIEF-2026-00001", decided_by="markus")
        assert result is not None

    def test_approve_rejects_unvalidated(self):
        record  = _make_record(validation_status="pending", approval_status="PENDING_VALIDATION")
        service = self._make_service(record)
        with pytest.raises(BriefNotValidatedError):
            service.approve("BRIEF-2026-00001", decided_by="markus")

    def test_approve_rejects_already_approved(self):
        record  = _make_record(validation_status="passed", approval_status="APPROVED")
        service = self._make_service(record)
        with pytest.raises(AlreadyApprovedError):
            service.approve("BRIEF-2026-00001", decided_by="markus")

    def test_reject_requires_reason(self):
        record  = _make_record(validation_status="passed", approval_status="PENDING_VALIDATION")
        service = self._make_service(record)
        with pytest.raises(ValueError):
            service.reject("BRIEF-2026-00001", decided_by="markus", reason="")

    def test_reject_ok_with_reason(self):
        record  = _make_record(validation_status="passed", approval_status="PENDING_VALIDATION")
        service = self._make_service(record)
        result  = service.reject("BRIEF-2026-00001", decided_by="markus", reason="Motiv passt nicht")
        assert result is not None

    def test_invalid_transition_rejected(self):
        # SUPERSEDED → APPROVED ist verboten
        record  = _make_record(validation_status="passed", approval_status="SUPERSEDED")
        service = self._make_service(record)
        with pytest.raises(InvalidTransitionError):
            service.approve("BRIEF-2026-00001", decided_by="markus")

    def test_final_brief_raises(self):
        record  = _make_record(is_final=True, approval_status="APPROVED")
        service = self._make_service(record)
        with pytest.raises(BriefFinalError):
            service.approve("BRIEF-2026-00001", decided_by="markus")

    def test_revision_requires_instructions(self):
        record  = _make_record(validation_status="passed", approval_status="APPROVED")
        service = self._make_service(record)
        with pytest.raises(ValueError):
            service.request_revision("BRIEF-2026-00001", decided_by="markus", revision_instructions="")

    def test_skip_validation_check(self):
        record  = _make_record(validation_status="failed", approval_status="PENDING_VALIDATION")
        service = self._make_service(record)
        # Sollte trotz failed-Validation klappen wenn skip=True
        result  = service.approve(
            "BRIEF-2026-00001",
            decided_by="admin",
            skip_validation_check=True,
        )
        assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# BriefLockService — Unit Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBriefLockServiceDevMode:
    """BriefLockService im Dev-Modus ohne DB."""

    def _make_service(self, record: ContentBriefRecord) -> BriefLockService:
        mock_vm = MagicMock()
        mock_vm.get_latest.return_value = record
        return BriefLockService(db=None, version_manager=mock_vm)

    def test_lock_ok(self):
        record  = _make_record(production_blocked=False, approval_status="APPROVED")
        service = self._make_service(record)
        # Kein Fehler erwartet
        service.lock("BRIEF-2026-00001", actor="system", reason="Compliance-Check")

    def test_unlock_ok_when_approved(self):
        record  = _make_record(production_blocked=True, approval_status="APPROVED")
        service = self._make_service(record)
        service.unlock("BRIEF-2026-00001", actor="markus")

    def test_unlock_fails_when_not_approved(self):
        record  = _make_record(production_blocked=True, approval_status="PENDING_VALIDATION")
        service = self._make_service(record)
        with pytest.raises(UnlockNotAllowedError):
            service.unlock("BRIEF-2026-00001", actor="markus")

    def test_unlock_skip_check(self):
        record  = _make_record(production_blocked=True, approval_status="PENDING_VALIDATION")
        service = self._make_service(record)
        # Mit skip=True kein Fehler
        service.unlock("BRIEF-2026-00001", actor="admin", skip_approval_check=True)

    def test_start_production_ok(self):
        record  = _make_record(production_blocked=False, approval_status="APPROVED")
        service = self._make_service(record)
        result  = service.start_production("BRIEF-2026-00001", actor="image-job-runner")
        assert result is not None

    def test_start_production_fails_when_blocked(self):
        record  = _make_record(production_blocked=True, approval_status="APPROVED")
        service = self._make_service(record)
        with pytest.raises(LockError):
            service.start_production("BRIEF-2026-00001", actor="image-job-runner")

    def test_finalize_fails_without_production_started(self):
        record  = _make_record(production_started_at=None, is_final=False)
        service = self._make_service(record)
        with pytest.raises(ProductionNotStartedError):
            service.finalize("BRIEF-2026-00001", actor="image-job-runner")

    def test_finalize_ok(self):
        record  = _make_record(
            production_started_at=datetime.now(timezone.utc),
            is_final=False,
        )
        service = self._make_service(record)
        result  = service.finalize("BRIEF-2026-00001", actor="image-job-runner")
        assert result is not None

    def test_finalize_fails_if_already_final(self):
        record  = _make_record(
            production_started_at=datetime.now(timezone.utc),
            is_final=True,
        )
        service = self._make_service(record)
        with pytest.raises((BriefFinalError, AlreadyFinalError)):
            service.finalize("BRIEF-2026-00001", actor="image-job-runner")

    def test_get_lock_status_no_db(self):
        record  = _make_record(production_blocked=True, approval_status="APPROVED")
        service = self._make_service(record)
        status  = service.get_lock_status("BRIEF-2026-00001")
        assert status["found"] is True
        assert status["production_blocked"] is True
        assert status["approval_status"] == "APPROVED"


# ─────────────────────────────────────────────────────────────────────────────
# Singleton-Accessors
# ─────────────────────────────────────────────────────────────────────────────

class TestSingletonAccessors:
    def test_approval_service_singleton(self):
        s1 = get_brief_approval_service()
        s2 = get_brief_approval_service()
        assert s1 is s2

    def test_lock_service_singleton(self):
        s1 = get_brief_lock_service()
        s2 = get_brief_lock_service()
        assert s1 is s2

    def test_approval_service_new_instance_with_db(self):
        mock_db = MagicMock()
        s = get_brief_approval_service(db=mock_db)
        assert s._db is mock_db

    def test_lock_service_new_instance_with_db(self):
        mock_db = MagicMock()
        s = get_brief_lock_service(db=mock_db)
        assert s._db is mock_db
