"""
Testdaten für Content-Brief-Validierungstests.
"""

from uuid import UUID

from app.models.content_brief_validation import (
    BriefValidationInput,
    CategoryScores,
    ValidationError,
    ValidationWarning,
)

# ─────────────────────────────────────────────────────────────────────────────
# Brief-JSONs
# ─────────────────────────────────────────────────────────────────────────────

VALID_BRIEF_JSON = {
    "brief_id": "BRIEF-2026-001",
    "schema_version": "1.0.0",
    "brief": {
        "platform": "instagram",
        "content_type": "reel",
        "hook": {
            "text": "Eines Tages werden sie nicht mehr malen.",
            "type": "verlust",
            "delivery": "text_overlay",
            "timing_sec": 3,
        },
        "main_emotion": "Nostalgie",
        "story_structure": {
            "type": "drei_akt",
            "act_1": {
                "description": "Kind beim Malen — vertrauter Alltagsmoment",
                "duration_pct": 20,
                "emotional_target": "Wiedererkennung",
            },
            "act_2": {
                "description": "Zeichnung wird zur 3D-Figur",
                "duration_pct": 50,
                "emotional_target": "Spannung",
            },
            "act_3": {
                "description": "Reaktion. Fertige Figur neben Zeichnung.",
                "duration_pct": 30,
                "emotional_target": "Rührung",
            },
        },
        "scene_structure": [
            {"scene_id": 1, "description": "Kinderhände beim Malen", "duration_sec": 5},
            {"scene_id": 2, "description": "Zeichnung in Nahaufnahme", "duration_sec": 4},
            {"scene_id": 3, "description": "Entstehung der 3D-Figur", "duration_sec": 10},
            {"scene_id": 4, "description": "Reveal: Figur neben Zeichnung", "duration_sec": 5},
            {"scene_id": 5, "description": "Emotionaler Abschluss", "duration_sec": 3},
        ],
        "platform_format": {
            "platform": "instagram",
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
        },
        "brand_style": {
            "tone": "warm_artisanal_emotional",
            "do": ["Handwerk zeigen"],
            "dont": ["Preise nennen"],
        },
    },
}

BRIEF_JSON_MISSING_HOOK = {
    "brief_id": "BRIEF-2026-002",
    "schema_version": "1.0.0",
    "brief": {
        "platform": "instagram",
        "content_type": "reel",
        "hook": {"text": "", "type": "verlust"},
        "scene_structure": [
            {"scene_id": 1, "description": "Test", "duration_sec": 5},
            {"scene_id": 2, "description": "Test 2", "duration_sec": 5},
            {"scene_id": 3, "description": "Test 3", "duration_sec": 5},
        ],
        "platform_format": {"platform": "instagram"},
    },
}

BRIEF_JSON_MISSING_PLATFORM = {
    "brief_id": "BRIEF-2026-003",
    "schema_version": "1.0.0",
    "brief": {
        "platform": "",
        "content_type": "reel",
        "hook": {"text": "Ein guter Hook.", "type": "verlust"},
        "scene_structure": [
            {"scene_id": 1, "description": "Test", "duration_sec": 5},
            {"scene_id": 2, "description": "Test 2", "duration_sec": 5},
            {"scene_id": 3, "description": "Test 3", "duration_sec": 5},
        ],
        "platform_format": {},
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Kategorie-Scores für verschiedene Szenarien
# ─────────────────────────────────────────────────────────────────────────────

SCORES_PASSED = CategoryScores(
    hook_score=9.0,
    story_score=8.5,
    platform_fit_score=8.5,
    brand_fit_score=9.0,
    clarity_score=8.0,
    production_realism_score=8.5,
    risk_score=9.0,
)
# Erwarteter Score: 8.775 → passed

SCORES_WARNING = CategoryScores(
    hook_score=7.0,
    story_score=7.0,
    platform_fit_score=7.5,
    brand_fit_score=7.0,
    clarity_score=6.5,
    production_realism_score=7.0,
    risk_score=8.0,
)
# Erwarteter Score: ~7.1 → warning

SCORES_FAILED = CategoryScores(
    hook_score=4.0,
    story_score=5.0,
    platform_fit_score=5.0,
    brand_fit_score=4.5,
    clarity_score=4.0,
    production_realism_score=6.0,
    risk_score=7.0,
)
# Erwarteter Score: ~4.8 → failed

SCORES_BRAND_FIT_LOW = CategoryScores(
    hook_score=8.0,
    story_score=8.0,
    platform_fit_score=8.0,
    brand_fit_score=4.0,   # < 5 → HARD ERROR BRAND_FIT_TOO_LOW
    clarity_score=8.0,
    production_realism_score=8.0,
    risk_score=8.0,
)

# ─────────────────────────────────────────────────────────────────────────────
# BriefValidationInput-Fixtures
# ─────────────────────────────────────────────────────────────────────────────

BRIEF_UUID_PASSED  = UUID("a3f7c1d2-0000-0000-0000-000000000001")
BRIEF_UUID_WARNING = UUID("a3f7c1d2-0000-0000-0000-000000000002")
BRIEF_UUID_FAILED  = UUID("a3f7c1d2-0000-0000-0000-000000000003")
BRIEF_UUID_NO_HOOK = UUID("a3f7c1d2-0000-0000-0000-000000000004")

INPUT_PASSED = BriefValidationInput(
    brief_uuid=BRIEF_UUID_PASSED,
    brief_id="BRIEF-2026-001",
    brief_version=1,
    category_scores=SCORES_PASSED,
    errors=[],
    warnings=[],
    validation_notes="Sehr guter Brief. Alle Kernelemente stark.",
    validated_by="qc-agent",
    brief_json=VALID_BRIEF_JSON,       # Hard-Error-Check läuft gegen echten Brief
)

INPUT_WARNING = BriefValidationInput(
    brief_uuid=BRIEF_UUID_WARNING,
    brief_id="BRIEF-2026-002",
    brief_version=1,
    category_scores=SCORES_WARNING,
    errors=[],
    warnings=[
        ValidationWarning(
            code="HOOK_WEAK",
            field="brief.hook.text",
            message="Hook ist mittelmäßig — geringe Emotionsdichte.",
            suggestion="Stärkere Emotionssprache oder Frage-Hook verwenden.",
        ),
        ValidationWarning(
            code="STORY_TOO_COMPLEX",
            field="brief.story_structure",
            message="Story hat zu viele Handlungsstränge für Reel-Format.",
            suggestion="Auf einen klaren Protagonist-Moment reduzieren.",
        ),
    ],
    validation_notes="Brief akzeptabel aber verbesserungswürdig.",
    validated_by="qc-agent",
    brief_json=VALID_BRIEF_JSON,       # Hard-Error-Check läuft gegen echten Brief
)

INPUT_FAILED = BriefValidationInput(
    brief_uuid=BRIEF_UUID_FAILED,
    brief_id="BRIEF-2026-003",
    brief_version=1,
    category_scores=SCORES_FAILED,
    errors=[
        ValidationError(
            code="MISSING_HOOK",
            field="brief.hook.text",
            message="Hook-Text fehlt oder ist leer.",
        ),
    ],
    warnings=[],
    validation_notes="Brief nicht produzierbar — kritische Fehler.",
    validated_by="qc-agent",
)
