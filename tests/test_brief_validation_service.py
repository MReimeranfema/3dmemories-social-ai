"""
Unit Tests: BriefValidationService + ValidationScoreCalculator

Tests laufen vollständig in-memory — kein DB, kein Sheets, kein Slack.
Run: pytest tests/test_brief_validation_service.py -v
"""

import pytest
from decimal import Decimal
from uuid import UUID, uuid4

from app.models.content_brief_validation import (
    BriefValidationInput,
    CategoryScores,
    ValidationError,
    ValidationResult,
    ValidationScoreCalculator,
    ValidationWarning,
)
from app.services.brief_validation_service import (
    BriefNotFoundError,
    BriefValidationService,
)
from tests.fixtures.brief_validation_fixtures import (
    BRIEF_JSON_MISSING_HOOK,
    BRIEF_JSON_MISSING_PLATFORM,
    BRIEF_UUID_FAILED,
    BRIEF_UUID_PASSED,
    BRIEF_UUID_WARNING,
    INPUT_FAILED,
    INPUT_PASSED,
    INPUT_WARNING,
    SCORES_BRAND_FIT_LOW,
    SCORES_FAILED,
    SCORES_PASSED,
    SCORES_WARNING,
    VALID_BRIEF_JSON,
)


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktion: Service ohne DB instanziieren
# ─────────────────────────────────────────────────────────────────────────────

def make_service() -> BriefValidationService:
    return BriefValidationService(db=None, sheets_client=None, slack_client=None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Score-Berechnung
# ─────────────────────────────────────────────────────────────────────────────

class TestValidationScoreCalculator:

    def test_passed_score_above_threshold(self):
        score, status = ValidationScoreCalculator.calculate(SCORES_PASSED, errors=[])
        assert score >= ValidationScoreCalculator.THRESHOLD_PASSED
        assert status == ValidationResult.PASSED

    def test_warning_score_in_range(self):
        score, status = ValidationScoreCalculator.calculate(SCORES_WARNING, errors=[])
        assert ValidationScoreCalculator.THRESHOLD_WARNING <= score < ValidationScoreCalculator.THRESHOLD_PASSED
        assert status == ValidationResult.WARNING

    def test_failed_score_below_threshold(self):
        score, status = ValidationScoreCalculator.calculate(SCORES_FAILED, errors=[])
        assert score < ValidationScoreCalculator.THRESHOLD_WARNING
        assert status == ValidationResult.FAILED

    def test_hard_error_forces_failed_regardless_of_score(self):
        """Auch sehr hohe Kategorie-Scores → failed wenn harter Fehler vorliegt."""
        hard_error = ValidationError(
            code="MISSING_HOOK",
            field="brief.hook.text",
            message="Hook fehlt.",
        )
        score, status = ValidationScoreCalculator.calculate(SCORES_PASSED, errors=[hard_error])
        assert status == ValidationResult.FAILED
        # Score selbst bleibt hoch — nur Status ist forced
        assert score >= ValidationScoreCalculator.THRESHOLD_PASSED

    def test_brand_fit_below_5_triggers_hard_error(self):
        """brand_fit_score < 5 → BRAND_FIT_TOO_LOW Hard Error."""
        hard_error = ValidationError(
            code="BRAND_FIT_TOO_LOW",
            field="brief.brand_style",
            message="Brand Fit unter 5.",
        )
        _, status = ValidationScoreCalculator.calculate(SCORES_BRAND_FIT_LOW, errors=[hard_error])
        assert status == ValidationResult.FAILED

    def test_score_is_weighted_average(self):
        """Score muss der gewichteten Summe entsprechen."""
        scores = CategoryScores(
            hook_score=10.0,
            story_score=10.0,
            platform_fit_score=10.0,
            brand_fit_score=10.0,
            clarity_score=10.0,
            production_realism_score=10.0,
            risk_score=10.0,
        )
        score, _ = ValidationScoreCalculator.calculate(scores, errors=[])
        assert score == 10.0

    def test_score_all_zeros(self):
        scores = CategoryScores(
            hook_score=0, story_score=0, platform_fit_score=0, brand_fit_score=0,
            clarity_score=0, production_realism_score=0, risk_score=0,
        )
        score, status = ValidationScoreCalculator.calculate(scores, errors=[])
        assert score == 0.0
        assert status == ValidationResult.FAILED

    def test_weights_sum_to_one(self):
        total = sum(ValidationScoreCalculator.WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Gewichte summieren nicht auf 1.0: {total}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Hard-Error-Erkennung aus brief_json
# ─────────────────────────────────────────────────────────────────────────────

class TestHardErrorDetection:

    def test_missing_hook_detected(self):
        errors = ValidationScoreCalculator.check_hard_errors_from_brief(BRIEF_JSON_MISSING_HOOK)
        codes = {e.code for e in errors}
        assert "MISSING_HOOK" in codes

    def test_missing_platform_detected(self):
        errors = ValidationScoreCalculator.check_hard_errors_from_brief(BRIEF_JSON_MISSING_PLATFORM)
        codes = {e.code for e in errors}
        assert "MISSING_PLATFORM" in codes

    def test_valid_brief_no_hard_errors(self):
        errors = ValidationScoreCalculator.check_hard_errors_from_brief(VALID_BRIEF_JSON)
        assert len(errors) == 0

    def test_missing_scene_structure(self):
        brief = {"brief": {"hook": {"text": "Test"}, "platform": "instagram",
                            "content_type": "reel", "scene_structure": [],
                            "platform_format": {}}}
        errors = ValidationScoreCalculator.check_hard_errors_from_brief(brief)
        codes = {e.code for e in errors}
        assert "MISSING_SCENE_STRUCTURE" in codes

    def test_missing_platform_format(self):
        brief = {"brief": {"hook": {"text": "Test"}, "platform": "instagram",
                            "content_type": "reel",
                            "scene_structure": [{"scene_id": 1}],
                            "platform_format": {}}}
        errors = ValidationScoreCalculator.check_hard_errors_from_brief(brief)
        codes = {e.code for e in errors}
        assert "MISSING_PLATFORM_FORMAT" in codes


# ─────────────────────────────────────────────────────────────────────────────
# 3. BriefValidationService — Kern-Ablauf
# ─────────────────────────────────────────────────────────────────────────────

class TestBriefValidationService:

    def test_passed_brief_returns_passed_status(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_PASSED)
        assert record.validation_status == ValidationResult.PASSED
        assert record.production_blocked is False

    def test_warning_brief_returns_warning_status(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_WARNING)
        assert record.validation_status == ValidationResult.WARNING
        assert record.production_blocked is False

    def test_failed_brief_is_production_blocked(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_FAILED)
        assert record.validation_status == ValidationResult.FAILED
        assert record.production_blocked is True

    def test_record_contains_correct_brief_id(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_PASSED)
        assert record.brief_id == INPUT_PASSED.brief_id

    def test_record_contains_correct_version(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_PASSED)
        assert record.brief_version == INPUT_PASSED.brief_version

    def test_record_uuid_is_unique_per_call(self):
        svc = make_service()
        r1 = svc.validate_and_store_brief(INPUT_PASSED)
        r2 = svc.validate_and_store_brief(INPUT_PASSED)
        assert r1.id != r2.id

    def test_warnings_stored_in_record(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_WARNING)
        assert len(record.validation_warnings) == 2
        codes = [w["code"] for w in record.validation_warnings]
        assert "HOOK_WEAK" in codes
        assert "STORY_TOO_COMPLEX" in codes

    def test_errors_stored_in_record(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_FAILED)
        assert len(record.validation_errors) >= 1
        codes = [e["code"] for e in record.validation_errors]
        assert "MISSING_HOOK" in codes

    def test_category_scores_preserved(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_PASSED)
        assert record.hook_score               == SCORES_PASSED.hook_score
        assert record.story_score              == SCORES_PASSED.story_score
        assert record.brand_fit_score          == SCORES_PASSED.brand_fit_score
        assert record.production_realism_score == SCORES_PASSED.production_realism_score

    def test_validated_by_stored(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_PASSED)
        assert record.validated_by == "qc-agent"

    def test_created_at_is_set(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_PASSED)
        assert record.created_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Produktionsregeln
# ─────────────────────────────────────────────────────────────────────────────

class TestProductionRules:

    def test_passed_can_proceed_to_production(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_PASSED)
        assert record.can_proceed_to_production is True
        assert record.requires_manual_approval is False
        assert record.is_hard_blocked is False

    def test_warning_can_proceed_but_requires_approval(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_WARNING)
        assert record.can_proceed_to_production is True
        assert record.requires_manual_approval is True
        assert record.is_hard_blocked is False

    def test_failed_cannot_proceed(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_FAILED)
        assert record.can_proceed_to_production is False
        assert record.requires_manual_approval is False
        assert record.is_hard_blocked is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. Fehler-Deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorMerging:

    def test_duplicate_error_codes_deduplicated(self):
        """Wenn input und detected denselben Code haben, wird nur einer gespeichert."""
        err_from_input = ValidationError(
            code="MISSING_HOOK", field="brief.hook.text", message="Original."
        )
        err_detected = ValidationError(
            code="MISSING_HOOK", field="brief.hook.text", message="Detected version."
        )
        merged = BriefValidationService._merge_errors([err_from_input], [err_detected])
        assert len([e for e in merged if e.code == "MISSING_HOOK"]) == 1
        # detected überschreibt provided
        assert any(e.message == "Detected version." for e in merged)

    def test_unique_errors_all_preserved(self):
        err1 = ValidationError(code="ERR_A", field="f", message="A")
        err2 = ValidationError(code="ERR_B", field="f", message="B")
        err3 = ValidationError(code="ERR_C", field="f", message="C")
        merged = BriefValidationService._merge_errors([err1], [err2, err3])
        codes = {e.code for e in merged}
        assert {"ERR_A", "ERR_B", "ERR_C"} == codes


# ─────────────────────────────────────────────────────────────────────────────
# 6. Slack-Nachricht-Format
# ─────────────────────────────────────────────────────────────────────────────

class TestSlackMessageFormat:

    def _get_record(self, inp):
        svc = make_service()
        return svc.validate_and_store_brief(inp)

    def test_failed_message_contains_blocked_indicator(self):
        svc = make_service()
        record = self._get_record(INPUT_FAILED)
        brief_row = {"platform": "instagram"}
        msg = svc._build_slack_message(record, brief_row)
        assert "BLOCKIERT" in msg or "FAILED" in msg
        assert record.brief_id in msg

    def test_warning_message_contains_manual_approval_hint(self):
        svc = make_service()
        record = self._get_record(INPUT_WARNING)
        brief_row = {"platform": "instagram"}
        msg = svc._build_slack_message(record, brief_row)
        assert "manuelle" in msg.lower() or "Freigabe" in msg
        assert record.brief_id in msg

    def test_passed_message_contains_approve_command(self):
        svc = make_service()
        record = self._get_record(INPUT_PASSED)
        brief_row = {"platform": "instagram"}
        msg = svc._build_slack_message(record, brief_row)
        assert "/approve-brief" in msg
        assert record.brief_id in msg

    def test_all_messages_contain_brief_id(self):
        svc = make_service()
        brief_row = {"platform": "instagram"}
        for inp in [INPUT_PASSED, INPUT_WARNING, INPUT_FAILED]:
            record = self._get_record(inp)
            msg = svc._build_slack_message(record, brief_row)
            assert inp.brief_id in msg


# ─────────────────────────────────────────────────────────────────────────────
# 7. Edge Cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_empty_errors_and_warnings_allowed(self):
        inp = BriefValidationInput(
            brief_uuid=uuid4(),
            brief_id="BRIEF-2026-099",
            brief_version=1,
            category_scores=SCORES_PASSED,
            errors=[],
            warnings=[],
            validated_by="qc-agent",
        )
        svc = make_service()
        record = svc.validate_and_store_brief(inp)
        assert record.validation_errors == []
        assert record.validation_warnings == []

    def test_validation_notes_optional(self):
        inp = BriefValidationInput(
            brief_uuid=uuid4(),
            brief_id="BRIEF-2026-098",
            brief_version=1,
            category_scores=SCORES_PASSED,
            validated_by="qc-agent",
        )
        svc = make_service()
        record = svc.validate_and_store_brief(inp)
        assert record.validation_notes is None

    def test_score_rounded_to_two_decimals(self):
        svc = make_service()
        record = svc.validate_and_store_brief(INPUT_PASSED)
        score_str = str(record.validation_score)
        decimal_places = len(score_str.split(".")[-1]) if "." in score_str else 0
        assert decimal_places <= 2

    def test_invalid_brief_id_raises_validation_error(self):
        with pytest.raises(Exception):
            BriefValidationInput(
                brief_uuid=uuid4(),
                brief_id="INVALID-ID",
                brief_version=1,
                category_scores=SCORES_PASSED,
                validated_by="qc-agent",
            )

    def test_score_boundary_exactly_at_passed_threshold(self):
        """Score exakt 8.0 → passed."""
        # Kalibriere Scores so dass gewichteter Score ≈ 8.0
        scores = CategoryScores(
            hook_score=8.0, story_score=8.0, platform_fit_score=8.0,
            brand_fit_score=8.0, clarity_score=8.0,
            production_realism_score=8.0, risk_score=8.0,
        )
        score, status = ValidationScoreCalculator.calculate(scores, errors=[])
        assert score == 8.0
        assert status == ValidationResult.PASSED

    def test_score_boundary_exactly_at_warning_threshold(self):
        """Score exakt 6.5 → warning."""
        scores = CategoryScores(
            hook_score=6.5, story_score=6.5, platform_fit_score=6.5,
            brand_fit_score=6.5, clarity_score=6.5,
            production_realism_score=6.5, risk_score=6.5,
        )
        score, status = ValidationScoreCalculator.calculate(scores, errors=[])
        assert score == 6.5
        assert status == ValidationResult.WARNING
