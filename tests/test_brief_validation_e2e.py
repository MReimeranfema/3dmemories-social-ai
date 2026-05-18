"""
E2E-Integrationstest: vollständige Validierungs-Pipeline

Schritte die geprüft werden:
  1. Brief erzeugen
  2. Validierung starten
  3. Ergebnis in content_briefs speichern (via DB-Trigger-Simulation)
  4. Historieneintrag in content_brief_validations schreiben
  5. Google Sheets aktualisieren
  6. Slack zeigt Status an

Run: pytest tests/test_brief_validation_e2e.py -v -s
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.models.content_brief_validation import (
    BriefValidationInput,
    CategoryScores,
    ValidationError,
    ValidationResult,
    ValidationScoreCalculator,
    ValidationWarning,
)
from app.services.brief_validation_service import BriefValidationService


# ─────────────────────────────────────────────────────────────────────────────
# Testdaten — vollständiger Brief (production-nahe Qualität)
# ─────────────────────────────────────────────────────────────────────────────

TEST_BRIEF_UUID = UUID("e2e00000-0000-0000-0000-000000000001")
TEST_BRIEF_ID   = "BRIEF-2026-042"
TEST_VERSION    = 1

FULL_BRIEF_JSON = {
    "brief_id": TEST_BRIEF_ID,
    "schema_version": "1.0.0",
    "created_at": "2026-05-17T14:30:00Z",
    "source_idea_id": "3DM-2026-042",
    "brief": {
        "platform": "instagram",
        "content_type": "reel",
        "target_audience": {
            "primary": "Eltern mit Kindern zwischen 2 und 10 Jahren",
            "secondary": "Großeltern, die Geschenke suchen",
            "age_range": "28–45",
            "emotional_state": "nostalgisch, sentimental",
        },
        "main_emotion": "Nostalgie",
        "hook": {
            "text": "Eines Tages werden sie nicht mehr malen. Heute tun sie es noch.",
            "type": "verlust",
            "delivery": "text_overlay",
            "timing_sec": 3,
        },
        "story_structure": {
            "type": "drei_akt",
            "act_1": {
                "name": "Ausgangssituation",
                "description": "Kind beim Malen — vertrauter Alltagsmoment",
                "duration_pct": 20,
                "emotional_target": "Wiedererkennung",
            },
            "act_2": {
                "name": "Transformation",
                "description": "Prozess: Zeichnung wird zu 3D-Objekt",
                "duration_pct": 50,
                "emotional_target": "Spannung und Vorfreude",
            },
            "act_3": {
                "name": "Auflösung",
                "description": "Fertige Figur. Reaktion. Stille oder Musik-Klimax.",
                "duration_pct": 30,
                "emotional_target": "Rührung, Befriedigung",
            },
        },
        "scene_structure": [
            {
                "scene_id": 1,
                "name": "Hook-Szene",
                "description": "Nahaufnahme: Kinderhände beim Malen",
                "duration_sec": 5,
                "camera": "extreme_close_up",
                "text_overlay": "Eines Tages werden sie nicht mehr malen.",
            },
            {
                "scene_id": 2,
                "name": "Zeichnung zeigen",
                "description": "Das fertige Bild in Fokus, leicht unscharfer Hintergrund",
                "duration_sec": 4,
                "camera": "overhead_flat_lay",
                "text_overlay": None,
            },
            {
                "scene_id": 3,
                "name": "Transformations-Prozess",
                "description": "Zeitraffer: Entstehung der 3D-Figur. Hände sichtbar.",
                "duration_sec": 10,
                "camera": "top_down_timelapse",
                "text_overlay": None,
            },
            {
                "scene_id": 4,
                "name": "Reveal",
                "description": "Fertige Figur aus gleichem Winkel wie Zeichnung",
                "duration_sec": 5,
                "camera": "overhead_flat_lay",
                "text_overlay": "Das Unmögliche, möglich gemacht.",
            },
            {
                "scene_id": 5,
                "name": "Emotionaler Abschluss",
                "description": "Zeichnung und Figur nebeneinander. Hand des Kindes.",
                "duration_sec": 3,
                "camera": "close_up_hands",
                "text_overlay": "@3dmemories.de",
            },
        ],
        "platform_format": {
            "platform": "instagram",
            "content_type": "reel",
            "aspect_ratio": "9:16",
            "resolution": "1080x1920",
            "fps": 30,
            "safe_zone_top_px": 150,
            "safe_zone_bottom_px": 200,
        },
        "video_length": {
            "target_sec": 27,
            "min_sec": 15,
            "max_sec": 35,
        },
        "brand_style": {
            "tone": "warm_artisanal_emotional",
            "do": ["Handwerk sichtbar machen", "Natürliche Materialien"],
            "dont": ["Preise nennen", "Stockfoto-Ästhetik"],
            "logo_usage": "nur_handle_im_letzten_frame",
            "watermark": False,
        },
        "production_complexity": {"score": 4, "estimated_shoot_time_min": 45},
        "production_risk": {
            "score": 2,
            "level": "niedrig",
            "flags": [],
            "compliance_notes": "Kein Kindgesicht zeigen. Nur Hände des Kindes.",
            "brand_risk": "niedrig",
        },
        "cta_style": {"type": "soft_implicit"},
        "audio_mood": {"type": "instrumental_background"},
        "visual_mood": {
            "texture_feel": "handmade_artisanal",
            "avoid": ["harsh_shadows", "neon_colors"],
        },
        "camera_style": {"primary": "overhead_flat_lay"},
        "reveal_moment": {
            "scene_id": 4,
            "type": "side_by_side_comparison",
            "timing_sec": 19,
            "intensity": "high",
        },
    },
}

STRONG_CATEGORY_SCORES = CategoryScores(
    hook_score=9.0,
    story_score=8.5,
    platform_fit_score=9.0,
    brand_fit_score=9.0,
    clarity_score=8.5,
    production_realism_score=8.0,
    risk_score=9.5,
)

MEDIUM_CATEGORY_SCORES = CategoryScores(
    hook_score=7.5,
    story_score=7.0,
    platform_fit_score=7.5,
    brand_fit_score=7.0,
    clarity_score=6.8,
    production_realism_score=7.0,
    risk_score=8.0,
)

WEAK_CATEGORY_SCORES = CategoryScores(
    hook_score=4.0,
    story_score=5.0,
    platform_fit_score=5.0,
    brand_fit_score=4.5,
    clarity_score=4.0,
    production_realism_score=6.0,
    risk_score=5.0,
)


# ─────────────────────────────────────────────────────────────────────────────
# Mock-Infrastruktur
# ─────────────────────────────────────────────────────────────────────────────

class MockDBRow(dict):
    """Simuliert ein SQLAlchemy Row-Objekt."""
    pass


class MockDB:
    """
    Simuliertes DB-Objekt.
    Protokolliert alle execute()-Aufrufe und gibt konfigurierbare Ergebnisse zurück.
    Simuliert den DB-Trigger: nach INSERT in content_brief_validations
    wird content_briefs automatisch aktualisiert (sync_brief_validation_status).
    """

    def __init__(self, brief_row: dict):
        self._brief_row = brief_row
        self.executed_statements: list[dict] = []     # Alle SQL-Aufrufe
        self.inserted_validations: list[dict] = []    # Nur INSERT in validations
        self.updated_briefs: list[dict] = []          # Nur UPDATE in content_briefs
        self.committed = False

    def execute(self, sql: str, params: dict | None = None):
        call = {"sql": sql.strip(), "params": params or {}}
        self.executed_statements.append(call)

        sql_upper = sql.strip().upper()

        if "INSERT INTO CONTENT_BRIEF_VALIDATIONS" in sql_upper:
            self.inserted_validations.append(params or {})
            # DB-Trigger-Simulation: sync zu content_briefs
            self._simulate_sync_trigger(params or {})
            return _MockCursor(None)

        if "UPDATE CONTENT_BRIEFS" in sql_upper:
            self.updated_briefs.append(params or {})
            return _MockCursor(None)

        if "SELECT" in sql_upper and "CONTENT_BRIEFS" in sql_upper:
            return _MockCursor(MockDBRow(self._brief_row))

        return _MockCursor(None)

    def _simulate_sync_trigger(self, validation_params: dict):
        """Simuliert trg_sync_validation_to_brief."""
        status = validation_params.get("status", "pending")
        production_blocked = status not in ("passed", "warning")
        update = {
            "brief_uuid":           self._brief_row["brief_uuid"],
            "validation_score":     validation_params.get("score"),
            "validation_status":    status,
            "production_blocked":   production_blocked,
            "_simulated_trigger":   True,
        }
        self._brief_row["production_blocked"] = production_blocked
        self._brief_row["validation_status"]  = status
        self.updated_briefs.append(update)

    def commit(self):
        self.committed = True

    @property
    def validation_insert(self) -> dict:
        """Gibt den ersten Validierungs-Insert zurück."""
        assert self.inserted_validations, "Kein INSERT in content_brief_validations!"
        return self.inserted_validations[0]


class _MockCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class MockSheetsClient:
    """Protokolliert alle update_row()-Aufrufe."""

    def __init__(self):
        self.calls: list[dict] = []

    def update_row(self, tab: str, match_column: str, match_value: str, updates: dict):
        self.calls.append({
            "tab":          tab,
            "match_column": match_column,
            "match_value":  match_value,
            "updates":      updates,
        })

    @property
    def last_call(self) -> dict:
        assert self.calls, "Kein Sheets-Update!"
        return self.calls[-1]


class MockSlackClient:
    """Protokolliert alle post_message()-Aufrufe."""

    def __init__(self):
        self.messages: list[str] = []

    def post_message(self, message: str):
        self.messages.append(message)

    @property
    def last_message(self) -> str:
        assert self.messages, "Keine Slack-Nachricht!"
        return self.messages[-1]


def make_brief_row(
    brief_uuid: UUID = TEST_BRIEF_UUID,
    brief_id: str = TEST_BRIEF_ID,
    version: int = TEST_VERSION,
    brief_json: dict = None,
) -> dict:
    """Erstellt eine DB-Zeile wie sie content_briefs liefern würde."""
    return {
        "brief_uuid":         str(brief_uuid),
        "brief_id":           brief_id,
        "version":            version,
        "brief_json":         brief_json or FULL_BRIEF_JSON,
        "platform":           "instagram",
        "content_type":       "reel",
        "hook":               FULL_BRIEF_JSON["brief"]["hook"]["text"],
        "is_deleted":         False,
        "validation_status":  "pending",
        "production_blocked": True,
    }


def make_service(
    brief_row: dict = None,
    sheets: MockSheetsClient = None,
    slack: MockSlackClient = None,
) -> tuple[BriefValidationService, MockDB, MockSheetsClient, MockSlackClient]:
    """Baut vollständig gemockten Service zusammen."""
    row         = brief_row  or make_brief_row()
    db          = MockDB(brief_row=row)
    sheets_mock = sheets     or MockSheetsClient()
    slack_mock  = slack      or MockSlackClient()
    svc = BriefValidationService(
        db=db, sheets_client=sheets_mock, slack_client=slack_mock
    )
    return svc, db, sheets_mock, slack_mock


# ─────────────────────────────────────────────────────────────────────────────
# E2E-Szenario-Helfer
# ─────────────────────────────────────────────────────────────────────────────

def run_full_pipeline(
    scores: CategoryScores,
    errors: list[ValidationError] = None,
    warnings: list[ValidationWarning] = None,
    brief_uuid: UUID = None,
    brief_id: str = None,
) -> tuple:
    """Führt die vollständige Pipeline für ein Szenario aus."""
    uuid   = brief_uuid or TEST_BRIEF_UUID
    bid    = brief_id   or TEST_BRIEF_ID
    svc, db, sheets, slack = make_service(brief_row=make_brief_row(brief_uuid=uuid, brief_id=bid))
    inp = BriefValidationInput(
        brief_uuid=uuid,
        brief_id=bid,
        brief_version=TEST_VERSION,
        category_scores=scores,
        errors=errors or [],
        warnings=warnings or [],
        validated_by="qc-agent",
        brief_json=FULL_BRIEF_JSON,
    )
    record = svc.validate_and_store_brief(inp)
    return record, db, sheets, slack


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1: Vollständige E2E-Pipeline — PASSED-Brief
# ─────────────────────────────────────────────────────────────────────────────

class TestE2EPipelinePassed:
    """
    Szenario: Hochwertiger Brief → passed → Freigabe-Flow.
    Erwartung: alle 6 Schritte laufen durch, Slack meldet grün.
    """

    def setup_method(self):
        self.record, self.db, self.sheets, self.slack = run_full_pipeline(
            scores=STRONG_CATEGORY_SCORES
        )

    # Schritt 1: Brief wurde erzeugt (geprüft über DB-Load)
    def test_schritt1_brief_geladen(self):
        """Brief-Daten wurden aus DB geladen (SELECT auf content_briefs)."""
        select_calls = [
            s for s in self.db.executed_statements
            if "SELECT" in s["sql"].upper() and "CONTENT_BRIEFS" in s["sql"].upper()
        ]
        assert len(select_calls) >= 1, "Kein SELECT auf content_briefs — Brief nicht geladen"
        print(f"\n✓ Schritt 1: Brief {TEST_BRIEF_ID} aus DB geladen")

    # Schritt 2: Validierung durchgeführt
    def test_schritt2_validierung_ergebnis(self):
        """Validierungsergebnis ist passed mit Score ≥ 8.0."""
        assert self.record.validation_status == ValidationResult.PASSED
        assert float(self.record.validation_score) >= 8.0
        print(f"\n✓ Schritt 2: Validierung abgeschlossen — Score {self.record.validation_score}/10 → PASSED")

    def test_schritt2_keine_hard_errors(self):
        """Valider Brief hat keine harten Fehler."""
        assert self.record.validation_errors == []
        print(f"\n✓ Schritt 2: Keine harten Fehler erkannt")

    # Schritt 3: content_briefs aktualisiert (via simuliertem DB-Trigger)
    def test_schritt3_content_briefs_aktualisiert(self):
        """DB-Trigger hat content_briefs.validation_status und production_blocked aktualisiert."""
        trigger_updates = [
            u for u in self.db.updated_briefs
            if u.get("_simulated_trigger") is True
        ]
        assert len(trigger_updates) == 1, "DB-Trigger hat content_briefs nicht aktualisiert"
        update = trigger_updates[0]
        assert update["validation_status"]  == "passed"
        assert update["production_blocked"] is False
        print(f"\n✓ Schritt 3: content_briefs aktualisiert — validation_status=passed, production_blocked=False")

    def test_schritt3_production_blocked_false(self):
        """passed-Brief ist nicht production_blocked."""
        assert self.record.production_blocked is False

    # Schritt 4: Historieneintrag in content_brief_validations
    def test_schritt4_historieneintrag_geschrieben(self):
        """Exakt ein INSERT in content_brief_validations."""
        assert len(self.db.inserted_validations) == 1, (
            f"Erwartet: 1 INSERT in content_brief_validations, "
            f"erhalten: {len(self.db.inserted_validations)}"
        )
        print(f"\n✓ Schritt 4: Historieneintrag in content_brief_validations geschrieben")

    def test_schritt4_historieneintrag_felder(self):
        """Insert enthält alle Pflichtfelder mit korrekten Werten."""
        insert = self.db.validation_insert
        assert insert["brief_id"]   == TEST_BRIEF_ID
        assert insert["brief_version"] == TEST_VERSION
        assert insert["status"]     == "passed"
        assert insert["validated_by"] == "qc-agent"
        # Score-Wert als String (für SQL-Parameter)
        assert float(insert["score"]) >= 8.0

    def test_schritt4_kategorie_scores_gespeichert(self):
        """Alle 7 Kategorie-Scores sind im Insert vorhanden."""
        insert = self.db.validation_insert
        assert insert["hook"]         == STRONG_CATEGORY_SCORES.hook_score
        assert insert["story"]        == STRONG_CATEGORY_SCORES.story_score
        assert insert["platform_fit"] == STRONG_CATEGORY_SCORES.platform_fit_score
        assert insert["brand_fit"]    == STRONG_CATEGORY_SCORES.brand_fit_score
        assert insert["clarity"]      == STRONG_CATEGORY_SCORES.clarity_score
        assert insert["prod_realism"] == STRONG_CATEGORY_SCORES.production_realism_score
        assert insert["risk"]         == STRONG_CATEGORY_SCORES.risk_score
        print(f"\n✓ Schritt 4: Alle 7 Kategorie-Scores korrekt gespeichert")

    def test_schritt4_db_committed(self):
        """Transaktion wurde committed."""
        assert self.db.committed is True

    # Schritt 5: Google Sheets aktualisiert
    def test_schritt5_sheets_aktualisiert(self):
        """Genau ein Sheets-Update wurde durchgeführt."""
        assert len(self.sheets.calls) == 1, (
            f"Erwartet: 1 Sheets-Update, erhalten: {len(self.sheets.calls)}"
        )
        print(f"\n✓ Schritt 5: Google Sheets Tab CONTENT_BRIEFS aktualisiert")

    def test_schritt5_sheets_tab_korrekt(self):
        """Update geht in den richtigen Tab."""
        call = self.sheets.last_call
        assert call["tab"] == "CONTENT_BRIEFS"
        assert call["match_column"] == "brief_id"
        assert call["match_value"]  == TEST_BRIEF_ID

    def test_schritt5_sheets_felder_komplett(self):
        """Alle 6 geforderten Sheets-Felder sind im Update enthalten."""
        updates = self.sheets.last_call["updates"]
        required = {
            "validation_score", "validation_status", "validation_notes",
            "validation_errors", "validation_warnings", "validated_at",
        }
        assert required <= set(updates.keys()), (
            f"Fehlende Sheets-Felder: {required - set(updates.keys())}"
        )
        assert updates["validation_status"] == "passed"
        assert float(updates["validation_score"]) >= 8.0
        print(f"\n✓ Schritt 5: Alle 6 Validierungsspalten in Sheets geschrieben")

    def test_schritt5_sheets_validated_at_iso_format(self):
        """validated_at ist ein valider ISO-Timestamp."""
        updates = self.sheets.last_call["updates"]
        ts = updates["validated_at"]
        parsed = datetime.fromisoformat(ts)
        assert parsed.tzinfo is not None, "Timestamp muss timezone-aware sein"

    # Schritt 6: Slack zeigt Status
    def test_schritt6_slack_nachricht_gesendet(self):
        """Genau eine Slack-Nachricht wurde gesendet."""
        assert len(self.slack.messages) == 1, (
            f"Erwartet: 1 Slack-Nachricht, erhalten: {len(self.slack.messages)}"
        )
        print(f"\n✓ Schritt 6: Slack-Nachricht gesendet")

    def test_schritt6_slack_zeigt_approved_status(self):
        """Slack-Nachricht zeigt VALIDE / bereit für Freigabe."""
        msg = self.slack.last_message
        assert "✅" in msg or "VALIDE" in msg
        assert TEST_BRIEF_ID in msg
        assert "/approve-brief" in msg
        print(f"\n✓ Schritt 6: Slack zeigt grünen Status mit Approve-Befehl")
        print(f"\n  Slack-Nachricht:\n  {msg}")

    def test_pipeline_vollstaendig(self):
        """Alle Schritte: SELECT + INSERT + UPDATE (Trigger) + Sheets + Slack."""
        assert len(self.db.executed_statements)  >= 2   # SELECT + INSERT
        assert len(self.db.inserted_validations) == 1
        assert len(self.db.updated_briefs)       >= 1   # Trigger-Update
        assert len(self.sheets.calls)            == 1
        assert len(self.slack.messages)          == 1
        print(f"\n✓ Vollständige Pipeline: alle 6 Schritte verifiziert")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2: E2E-Pipeline — WARNING-Brief
# ─────────────────────────────────────────────────────────────────────────────

class TestE2EPipelineWarning:
    """
    Szenario: Brief mit mittelmäßigen Scores → warning.
    Erwartung: production_blocked=False, aber manuelle Freigabe nötig.
    """

    def setup_method(self):
        self.record, self.db, self.sheets, self.slack = run_full_pipeline(
            scores=MEDIUM_CATEGORY_SCORES,
            warnings=[
                ValidationWarning(
                    code="HOOK_WEAK",
                    field="brief.hook.text",
                    message="Hook ist mittelmäßig.",
                    suggestion="Stärkere Emotionssprache verwenden.",
                ),
            ],
        )

    def test_schritt2_status_warning(self):
        score, status = ValidationScoreCalculator.calculate(MEDIUM_CATEGORY_SCORES, errors=[])
        assert status == ValidationResult.WARNING
        assert self.record.validation_status == ValidationResult.WARNING
        print(f"\n✓ Score {self.record.validation_score}/10 → WARNING")

    def test_schritt3_production_nicht_blocked(self):
        """WARNING-Brief darf produziert werden — mit manueller Freigabe."""
        assert self.record.production_blocked is False
        assert self.record.requires_manual_approval is True
        print(f"\n✓ WARNING: production_blocked=False, manuelle Freigabe erforderlich")

    def test_schritt4_historieneintrag_status_warning(self):
        assert self.db.validation_insert["status"] == "warning"

    def test_schritt5_sheets_status_warning(self):
        assert self.sheets.last_call["updates"]["validation_status"] == "warning"

    def test_schritt6_slack_zeigt_warning_mit_manuellem_review(self):
        msg = self.slack.last_message
        assert "🟡" in msg or "WARNING" in msg or "manuelle" in msg.lower()
        assert TEST_BRIEF_ID in msg
        assert "/approve-brief" in msg
        print(f"\n✓ Slack-Nachricht (WARNING):\n  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3: E2E-Pipeline — FAILED-Brief (harter Fehler)
# ─────────────────────────────────────────────────────────────────────────────

class TestE2EPipelineFailed:
    """
    Szenario: Brief mit hartem Fehler (MISSING_HOOK) → failed.
    Erwartung: production_blocked=TRUE, Slack meldet BLOCKIERT.
    """

    def setup_method(self):
        # Brief ohne Hook — hard error
        brief_ohne_hook = dict(FULL_BRIEF_JSON)
        brief_ohne_hook = {
            **FULL_BRIEF_JSON,
            "brief": {**FULL_BRIEF_JSON["brief"], "hook": {"text": "", "type": "verlust"}},
        }
        uuid = UUID("e2e00000-0000-0000-0000-000000000002")
        row  = make_brief_row(brief_uuid=uuid, brief_id="BRIEF-2026-099", brief_json=brief_ohne_hook)
        svc, self.db, self.sheets, self.slack = make_service(brief_row=row)

        inp = BriefValidationInput(
            brief_uuid=uuid,
            brief_id="BRIEF-2026-099",
            brief_version=1,
            category_scores=WEAK_CATEGORY_SCORES,
            errors=[
                ValidationError(
                    code="MISSING_HOOK",
                    field="brief.hook.text",
                    message="Hook-Text fehlt oder ist leer.",
                )
            ],
            validated_by="qc-agent",
            brief_json=brief_ohne_hook,
        )
        self.record = svc.validate_and_store_brief(inp)

    def test_schritt2_status_failed(self):
        assert self.record.validation_status == ValidationResult.FAILED
        print(f"\n✓ Score {self.record.validation_score}/10 → FAILED")

    def test_schritt3_production_blocked_true(self):
        """FAILED-Brief ist hard-blocked — darf nicht produziert werden."""
        assert self.record.production_blocked is True
        assert self.record.can_proceed_to_production is False
        print(f"\n✓ production_blocked=True — Brief gesperrt")

    def test_schritt3_trigger_setzt_production_blocked_true(self):
        trigger_updates = [u for u in self.db.updated_briefs if u.get("_simulated_trigger")]
        assert trigger_updates[0]["production_blocked"] is True

    def test_schritt4_hard_error_im_historieneintrag(self):
        insert = self.db.validation_insert
        assert insert["status"] == "failed"
        errors = json.loads(insert["errors"])
        codes  = [e["code"] for e in errors]
        assert "MISSING_HOOK" in codes
        print(f"\n✓ Schritt 4: MISSING_HOOK-Fehler im Historieneintrag gespeichert")

    def test_schritt5_sheets_status_failed(self):
        assert self.sheets.last_call["updates"]["validation_status"] == "failed"
        errors_json = self.sheets.last_call["updates"]["validation_errors"]
        assert "MISSING_HOOK" in errors_json
        print(f"\n✓ Schritt 5: Sheets zeigt validation_status=failed mit Fehler-Details")

    def test_schritt6_slack_zeigt_blockiert(self):
        msg = self.slack.last_message
        assert "🔴" in msg or "BLOCKIERT" in msg or "FAILED" in msg
        assert "BRIEF-2026-099" in msg
        print(f"\n✓ Slack-Nachricht (FAILED/BLOCKIERT):\n  {msg}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4: Mehrere aufeinanderfolgende Validierungen (Versions-Simulation)
# ─────────────────────────────────────────────────────────────────────────────

class TestE2EMultipleValidations:
    """
    Szenario: Brief wird zweimal validiert (erste Version schlägt fehl,
    zweite Version besteht). Prüft Historieneintrag-Erzeugung.
    """

    def setup_method(self):
        uuid = UUID("e2e00000-0000-0000-0000-000000000003")
        row  = make_brief_row(brief_uuid=uuid, brief_id="BRIEF-2026-077")
        svc, self.db, self.sheets, self.slack = make_service(brief_row=row)

        base_args = dict(
            brief_uuid=uuid,
            brief_id="BRIEF-2026-077",
            validated_by="qc-agent",
            brief_json=FULL_BRIEF_JSON,
        )

        # Erste Validierung — fehlschlag
        inp_v1 = BriefValidationInput(
            brief_version=1,
            category_scores=WEAK_CATEGORY_SCORES,
            errors=[ValidationError(
                code="MISSING_HOOK", field="brief.hook.text", message="Hook fehlt."
            )],
            **base_args,
        )
        self.record_v1 = svc.validate_and_store_brief(inp_v1)

        # Zweite Validierung — bestanden (nach Revision)
        inp_v2 = BriefValidationInput(
            brief_version=2,
            category_scores=STRONG_CATEGORY_SCORES,
            errors=[],
            **base_args,
        )
        self.record_v2 = svc.validate_and_store_brief(inp_v2)

    def test_zwei_historieintraege_geschrieben(self):
        """Jede Validierung erzeugt einen eigenen Eintrag in content_brief_validations."""
        assert len(self.db.inserted_validations) == 2
        print(f"\n✓ 2 Validierungseinträge in content_brief_validations")

    def test_erster_eintrag_failed(self):
        assert self.db.inserted_validations[0]["status"] == "failed"
        assert int(self.db.inserted_validations[0]["brief_version"]) == 1

    def test_zweiter_eintrag_passed(self):
        assert self.db.inserted_validations[1]["status"] == "passed"
        assert int(self.db.inserted_validations[1]["brief_version"]) == 2

    def test_beide_sheets_updates_gesendet(self):
        """Jede Validierung aktualisiert Sheets."""
        assert len(self.sheets.calls) == 2
        assert self.sheets.calls[0]["updates"]["validation_status"] == "failed"
        assert self.sheets.calls[1]["updates"]["validation_status"] == "passed"
        print(f"\n✓ Beide Sheets-Updates korrekt: failed → passed")

    def test_beide_slack_nachrichten_gesendet(self):
        """Jede Validierung sendet eine Slack-Nachricht."""
        assert len(self.slack.messages) == 2
        assert "BLOCKIERT" in self.slack.messages[0] or "FAILED" in self.slack.messages[0]
        assert "✅" in self.slack.messages[1] or "VALIDE" in self.slack.messages[1]
        print(f"\n✓ Slack: Nachricht 1 = FAILED, Nachricht 2 = PASSED")

    def test_endstatus_passed(self):
        assert self.record_v2.validation_status == ValidationResult.PASSED
        assert self.record_v2.production_blocked is False


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5: Graceful Degradation — Sheets oder Slack schlägt fehl
# ─────────────────────────────────────────────────────────────────────────────

class TestE2EGracefulDegradation:
    """
    Szenario: Sheets- oder Slack-Client wirft Exception.
    Erwartung: Pipeline läuft durch — Fehler werden geloggt, nicht propagiert.
    """

    class BrokenSheetsClient:
        def update_row(self, **kwargs):
            raise ConnectionError("Sheets API nicht erreichbar")

    class BrokenSlackClient:
        def __init__(self):
            self.messages = []
        def post_message(self, message):
            raise TimeoutError("Slack-Timeout")

    def test_sheets_fehler_bricht_pipeline_nicht(self):
        row = make_brief_row()
        db  = MockDB(brief_row=row)
        svc = BriefValidationService(
            db=db,
            sheets_client=self.BrokenSheetsClient(),
            slack_client=MockSlackClient(),
        )
        inp = BriefValidationInput(
            brief_uuid=TEST_BRIEF_UUID,
            brief_id=TEST_BRIEF_ID,
            brief_version=1,
            category_scores=STRONG_CATEGORY_SCORES,
            validated_by="qc-agent",
            brief_json=FULL_BRIEF_JSON,
        )
        # Darf keine Exception werfen
        record = svc.validate_and_store_brief(inp)
        assert record.validation_status == ValidationResult.PASSED
        assert db.committed is True   # DB wurde trotzdem committed
        print(f"\n✓ Sheets-Fehler: Pipeline läuft durch, DB committed")

    def test_slack_fehler_bricht_pipeline_nicht(self):
        row = make_brief_row()
        db  = MockDB(brief_row=row)
        svc = BriefValidationService(
            db=db,
            sheets_client=MockSheetsClient(),
            slack_client=self.BrokenSlackClient(),
        )
        inp = BriefValidationInput(
            brief_uuid=TEST_BRIEF_UUID,
            brief_id=TEST_BRIEF_ID,
            brief_version=1,
            category_scores=STRONG_CATEGORY_SCORES,
            validated_by="qc-agent",
            brief_json=FULL_BRIEF_JSON,
        )
        record = svc.validate_and_store_brief(inp)
        assert record.validation_status == ValidationResult.PASSED
        assert db.committed is True
        print(f"\n✓ Slack-Fehler: Pipeline läuft durch, DB committed")

    def test_beide_fehlschlagen_pipeline_laeuft(self):
        row = make_brief_row()
        db  = MockDB(brief_row=row)
        svc = BriefValidationService(
            db=db,
            sheets_client=self.BrokenSheetsClient(),
            slack_client=self.BrokenSlackClient(),
        )
        inp = BriefValidationInput(
            brief_uuid=TEST_BRIEF_UUID,
            brief_id=TEST_BRIEF_ID,
            brief_version=1,
            category_scores=STRONG_CATEGORY_SCORES,
            validated_by="qc-agent",
            brief_json=FULL_BRIEF_JSON,
        )
        record = svc.validate_and_store_brief(inp)
        assert record is not None
        assert db.committed is True
        print(f"\n✓ Sheets + Slack fehlgeschlagen: Pipeline läuft durch")


# ─────────────────────────────────────────────────────────────────────────────
# Konsolidierter Übersichts-Test (für schnelle Verifikation)
# ─────────────────────────────────────────────────────────────────────────────

class TestE2EPipelineZusammenfassung:
    """
    Kompakter Smoke-Test: prüft alle 6 Schritte in einem einzigen Test.
    Gibt eine lesbare Schritt-für-Schritt-Ausgabe aus.
    """

    def test_alle_6_schritte_passed_brief(self, capsys):
        print("\n" + "═" * 60)
        print("E2E-PIPELINE TEST — PASSED-Brief")
        print("═" * 60)

        # ── Schritt 1: Brief erzeugen ──────────────────────────────
        record, db, sheets, slack = run_full_pipeline(scores=STRONG_CATEGORY_SCORES)
        selects = [s for s in db.executed_statements if "SELECT" in s["sql"].upper()]
        assert selects, "Brief wurde nicht aus DB geladen"
        print(f"\n[1] Brief erzeugen      ✓  {TEST_BRIEF_ID} v{TEST_VERSION} aus DB geladen")

        # ── Schritt 2: Validierung ─────────────────────────────────
        assert record.validation_status == ValidationResult.PASSED
        assert float(record.validation_score) >= 8.0
        print(f"[2] Validierung         ✓  Score {record.validation_score}/10 → {record.validation_status.upper()}")

        # ── Schritt 3: content_briefs gespeichert ──────────────────
        trigger = next((u for u in db.updated_briefs if u.get("_simulated_trigger")), None)
        assert trigger is not None
        assert trigger["validation_status"]  == "passed"
        assert trigger["production_blocked"] is False
        print(f"[3] content_briefs      ✓  validation_status=passed | production_blocked=False")

        # ── Schritt 4: Historieneintrag ────────────────────────────
        assert len(db.inserted_validations) == 1
        ins = db.validation_insert
        assert ins["status"]    == "passed"
        assert ins["brief_id"]  == TEST_BRIEF_ID
        assert db.committed     is True
        print(f"[4] Historieneintrag    ✓  1 Eintrag in content_brief_validations | committed")

        # ── Schritt 5: Sheets ─────────────────────────────────────
        assert len(sheets.calls) == 1
        upd = sheets.last_call["updates"]
        assert upd["validation_status"] == "passed"
        assert "validation_errors" in upd
        print(f"[5] Google Sheets       ✓  Tab CONTENT_BRIEFS | validation_status=passed | 6 Spalten")

        # ── Schritt 6: Slack ──────────────────────────────────────
        assert len(slack.messages) == 1
        msg = slack.last_message
        assert "✅" in msg or "VALIDE" in msg
        assert TEST_BRIEF_ID in msg
        print(f"[6] Slack               ✓  {msg[:60]}...")
        print("\n" + "═" * 60)
        print("ALLE 6 SCHRITTE BESTANDEN")
        print("═" * 60)
