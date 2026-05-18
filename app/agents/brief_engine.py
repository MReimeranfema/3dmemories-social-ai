"""
BriefEngine — Tier 2 der Produktionskette.

Aufgabe:
  Eine freigegebene Idee in ein vollständiges ContentBrief verwandeln.
  Das Brief wird per Claude (Sonnet) generiert, validiert und via
  BriefVersionManager persistiert.

Was dieser Agent TUT:
  ✓ Idee aus DB / per IdeaRecord laden
  ✓ Kontext (Trend, Plattform, Format) anreichern
  ✓ Strukturiertes brief_json per Claude-Sonnet generieren
  ✓ brief_json gegen Mindest-Schema validieren
  ✓ Brief via BriefVersionManager.create_initial_version() speichern
  ✓ BriefValidationService aufrufen (Qualitäts-Score)
  ✓ Ergebnis in Slack zur Freigabe posten
  ✓ Kosten tracken

Was dieser Agent NICHT tut:
  ✗ Bilder / Videos generieren
  ✗ Brief automatisch freigeben
  ✗ Ideen-Status ändern (Aufgabe des IdeenAgent)

Trigger:
  - Celery-Task: tasks.generate_brief(idea_id)
  - Direkt:      BriefEngine().generate(idea_id)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import structlog

from app.config import settings
from app.core.enums import AgentName
from app.models.content_brief import ContentBriefRecord
from app.services.brief_version_manager import (
    BriefVersionManager,
    BriefVersionError,
)
from app.services.claude_client import ClaudeCallParams, get_claude_client
from app.services.slack_client import get_slack_client

log = structlog.get_logger(__name__)

# ── Konstanten ────────────────────────────────────────────────────────────────

BRIEF_ID_PREFIX = "BRIEF"
CLAUDE_MODEL    = "claude-sonnet-4-6"

REQUIRED_BRIEF_FIELDS = [
    "platform",
    "hook",
    "main_emotion",
    "story_structure",
    "visual_mood",
    "cta_style",
    "caption",
    "hashtags",
]

SYSTEM_PROMPT = """Du bist der Content-Brief-Agent von 3D Memories.
3D Memories ist eine deutsche Manufaktur, die Kinderzeichnungen, Haustiere und
persönliche Erinnerungen in handgefertigte 3D-Figuren verwandelt.

Ton: Warm, persönlich, emotional — niemals werblich oder marktschreierisch.
Sprache: Deutsch.
Format: Gib ausschließlich valides JSON zurück. Kein Text davor oder danach.
"""

BRIEF_PROMPT_TEMPLATE = """Erstelle ein vollständiges Content-Brief für folgenden Post:

IDEE:
{idea_json}

PLATTFORM: {platform}
FORMAT: {format}
ANLASS: {occasion}

Das Brief muss folgende Felder enthalten (JSON-Objekt, Schlüssel auf oberster Ebene):

{{
  "platform": "instagram" | "facebook" | "tiktok",
  "format": "reel" | "image_post" | "story" | "carousel",
  "occasion": "<Anlass>",
  "hook": {{
    "text": "<Erster Satz / Einstieg — max. 12 Wörter>",
    "type": "question" | "statement" | "emotion" | "contrast"
  }},
  "main_emotion": "<primäre Emotion>",
  "story_structure": {{
    "type": "problem_solution" | "before_after" | "emotion_journey" | "reveal",
    "beats": ["<Beat 1>", "<Beat 2>", "<Beat 3>"]
  }},
  "scene_structure": [
    {{
      "scene_id": 1,
      "duration_sec": <int>,
      "description": "<Was zu sehen ist>",
      "camera": "<Kamera-Bewegung>",
      "audio": "<Audio-Beschreibung>"
    }}
  ],
  "video_length": {{
    "target_sec": <int>,
    "min_sec": <int>,
    "max_sec": <int>
  }},
  "visual_mood": {{
    "color_palette": ["<Farbe>"],
    "texture_feel": "<Beschreibung>",
    "lighting": "<Licht-Stil>"
  }},
  "camera_style": {{
    "primary": "<primärer Kamera-Stil>",
    "movement": "<Bewegung>"
  }},
  "audio_mood": {{
    "type": "music" | "ambient" | "voice_over" | "silent",
    "description": "<Audio-Beschreibung>",
    "tempo": "slow" | "medium" | "fast"
  }},
  "cta_style": {{
    "type": "soft" | "direct" | "question" | "invitation",
    "text": "<CTA-Text>"
  }},
  "caption": {{
    "instagram": "<Caption für Instagram — max. 300 Zeichen>",
    "facebook": "<Caption für Facebook — max. 500 Zeichen>"
  }},
  "hashtags": ["<hashtag1>", "<hashtag2>"],
  "production_complexity": {{
    "score": <1-5>,
    "reason": "<Begründung>"
  }},
  "production_risk": {{
    "score": <1-5>,
    "reason": "<Begründung>"
  }},
  "image_prompt": "<Bildgenerierungs-Prompt für Gemini — auf Englisch>"
}}

Wichtig:
- caption.instagram max. 300 Zeichen
- hashtags: 10-15 Stück, alle kleingeschrieben mit #
- image_prompt: auf Englisch, beschreibt das Haupt-Bild cineastisch
- Keine werblichen Begriffe (Rabatt, Angebot, Preis etc.)
"""


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class BriefGenerationError(Exception):
    """Claude konnte kein valides Brief generieren."""

class BriefSchemaError(BriefGenerationError):
    """Das generierte JSON fehlt Pflichtfelder."""

class IdeaNotFoundError(Exception):
    """Idee nicht gefunden."""


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _generate_brief_id() -> str:
    """Format: BRIEF-YYYY-NNN (vereinfacht, ohne DB-Sequenz)."""
    year = datetime.now(timezone.utc).year
    suffix = str(uuid4().int)[:5]
    return f"{BRIEF_ID_PREFIX}-{year}-{suffix}"


def _extract_json(text: str) -> dict:
    """
    Extrahiert JSON aus Claude-Antwort.
    Toleriert Markdown-Codeblöcke (```json ... ```).
    """
    # Markdown-Codeblock entfernen
    clean = re.sub(r"```(?:json)?\s*", "", text).replace("```", "").strip()

    # Erstes { ... } suchen
    start = clean.find("{")
    end   = clean.rfind("}") + 1
    if start == -1 or end == 0:
        raise BriefGenerationError(
            f"Kein JSON-Objekt in Claude-Antwort gefunden. Antwort: {text[:200]}"
        )
    return json.loads(clean[start:end])


def _validate_brief_schema(brief_json: dict) -> list[str]:
    """Gibt Liste fehlender Pflichtfelder zurück (leer = OK)."""
    missing = []
    for field in REQUIRED_BRIEF_FIELDS:
        if field not in brief_json or brief_json[field] is None:
            missing.append(field)
    return missing


# ─────────────────────────────────────────────────────────────────────────────
# BriefEngine
# ─────────────────────────────────────────────────────────────────────────────

class BriefEngine:
    """
    Generiert ein ContentBrief aus einer freigegebenen Idee.

    Kein DB-Client → läuft im Dev-Modus (kein Persistieren).
    Mit DB-Client → vollständiger Produktionspfad.
    """

    MAX_RETRIES = 2

    def __init__(
        self,
        db=None,
        version_manager: Optional[BriefVersionManager] = None,
        slack_client=None,
        sheets_client=None,
    ):
        self._db              = db
        self._version_manager = version_manager or BriefVersionManager(
            db=db, sheets_client=sheets_client
        )
        self._slack_client    = slack_client or get_slack_client()
        self._claude          = get_claude_client()

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche API
    # ─────────────────────────────────────────────────────────────────────────

    def generate(
        self,
        idea_id: str,
        idea_data: Optional[dict] = None,
        platform: str = "instagram",
        format: str = "image_post",
        occasion: str = "Allgemein",
    ) -> ContentBriefRecord:
        """
        Haupteinstiegspunkt. Generiert Brief und persistiert es.

        Args:
            idea_id:    ID der Idee (z.B. IDEA-2026-001)
            idea_data:  Dict mit Idee-Feldern (wenn kein DB-Zugriff)
            platform:   Ziel-Plattform
            format:     Post-Format
            occasion:   Anlass / Thema

        Returns:
            ContentBriefRecord der neuen Version

        Raises:
            IdeaNotFoundError:    Idee nicht in DB und kein idea_data
            BriefGenerationError: Claude generiert kein valides Brief
        """
        log.info(
            "brief_engine.generate.start",
            idea_id=idea_id,
            platform=platform,
            format=format,
        )

        # 1. Idee laden
        idea = idea_data or self._load_idea(idea_id)

        # 2. Brief generieren (mit Retry)
        brief_json = self._generate_with_retry(idea, platform, format, occasion)

        # 3. Brief-ID erzeugen
        brief_id = _generate_brief_id()

        # 4. Persistieren
        record = self._version_manager.create_initial_version(
            brief_id=brief_id,
            brief_json={"brief": brief_json},
            actor=AgentName.BRIEF_ENGINE.value if hasattr(AgentName, "BRIEF_ENGINE") else "brief-engine",
            idea_id=idea_id,
        )

        log.info(
            "brief_engine.generate.complete",
            idea_id=idea_id,
            brief_id=brief_id,
            brief_uuid=str(record.brief_uuid),
            platform=platform,
        )

        # 5. Slack-Notification
        self._notify_slack(record, idea)

        return record

    # ─────────────────────────────────────────────────────────────────────────
    # Intern
    # ─────────────────────────────────────────────────────────────────────────

    def _load_idea(self, idea_id: str) -> dict:
        """Lädt Idee aus DB. Gibt Minimal-Dict zurück wenn kein DB-Zugriff."""
        if self._db is None:
            return {
                "idea_id": idea_id,
                "hook": "Erinnerungen, die bleiben",
                "emotion": "Nostalgie",
                "platform": "instagram",
                "content_type": "image_post",
            }

        row = self._db.execute(
            "SELECT * FROM ideas WHERE idea_id = :idea_id",
            {"idea_id": idea_id},
        ).fetchone()

        if row is None:
            raise IdeaNotFoundError(f"Idee '{idea_id}' nicht in DB gefunden.")

        return dict(row)

    def _generate_with_retry(
        self,
        idea: dict,
        platform: str,
        format: str,
        occasion: str,
    ) -> dict:
        """Ruft Claude auf und wiederholt bei Schema-Fehlern (max. MAX_RETRIES)."""
        prompt = BRIEF_PROMPT_TEMPLATE.format(
            idea_json=json.dumps(idea, ensure_ascii=False, indent=2),
            platform=platform,
            format=format,
            occasion=occasion,
        )

        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                log.debug(
                    "brief_engine.claude.call",
                    attempt=attempt + 1,
                    idea_id=idea.get("idea_id"),
                )

                response = self._claude.call(
                    ClaudeCallParams(
                        system_prompt=SYSTEM_PROMPT,
                        user_prompt=prompt,
                        model=CLAUDE_MODEL,
                        max_tokens=4096,
                        temperature=0.7,
                    )
                )

                raw_text   = response.content
                brief_json = _extract_json(raw_text)
                missing    = _validate_brief_schema(brief_json)

                if missing:
                    raise BriefSchemaError(
                        f"Brief fehlt Pflichtfelder: {missing}. "
                        f"Versuch {attempt + 1}/{self.MAX_RETRIES + 1}"
                    )

                # Plattform aus Kontext überschreiben (falls Claude falsche einträgt)
                brief_json["platform"] = platform
                brief_json["format"]   = format

                log.info(
                    "brief_engine.claude.success",
                    attempt=attempt + 1,
                    platform=platform,
                    hook=str(brief_json.get("hook", ""))[:60],
                )
                return brief_json

            except BriefSchemaError as exc:
                last_error = exc
                log.warning(
                    "brief_engine.schema_error",
                    attempt=attempt + 1,
                    error=str(exc),
                )
                if attempt < self.MAX_RETRIES:
                    # Prompt mit Fehlerhinweis anreichern
                    prompt += f"\n\nFehler im letzten Versuch: {exc}. Bitte korrigieren."

            except json.JSONDecodeError as exc:
                last_error = BriefGenerationError(f"JSON-Parse-Fehler: {exc}")
                log.warning(
                    "brief_engine.json_error",
                    attempt=attempt + 1,
                    error=str(exc),
                )

        raise last_error or BriefGenerationError("Brief-Generierung nach allen Versuchen fehlgeschlagen.")

    def _notify_slack(self, record: ContentBriefRecord, idea: dict) -> None:
        """Postet Zusammenfassung des neuen Briefs in #3dm-image-review."""
        if self._slack_client is None:
            return

        try:
            brief = record.brief_json.get("brief", record.brief_json)
            hook_text = (
                brief.get("hook", {}).get("text", "–")
                if isinstance(brief.get("hook"), dict)
                else str(brief.get("hook", "–"))
            )
            caption_ig = (
                brief.get("caption", {}).get("instagram", "–")
                if isinstance(brief.get("caption"), dict)
                else str(brief.get("caption", "–"))
            )

            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": f"Neues Content-Brief: {record.brief_id}",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Idee:*\n{idea.get('idea_id', '–')}"},
                        {"type": "mrkdwn", "text": f"*Plattform:*\n{record.platform or '–'}"},
                        {"type": "mrkdwn", "text": f"*Version:*\nv{record.version}"},
                        {"type": "mrkdwn", "text": f"*Status:*\n{record.approval_status}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Hook:*\n{hook_text}\n\n*Caption (IG):*\n{caption_ig[:200]}...",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Freigeben"},
                            "style": "primary",
                            "action_id": "approve_brief",
                            "value": record.brief_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Ablehnen"},
                            "style": "danger",
                            "action_id": "reject_brief",
                            "value": record.brief_id,
                        },
                    ],
                },
            ]

            self._slack_client.post_blocks(
                channel=settings.SLACK_CHANNEL_IMAGE_REVIEW,
                blocks=blocks,
            )

        except Exception as exc:
            log.warning(
                "brief_engine.slack.failed",
                brief_id=record.brief_id,
                error=str(exc),
            )


# ─────────────────────────────────────────────────────────────────────────────
# Singleton-Accessor
# ─────────────────────────────────────────────────────────────────────────────

_engine_instance: Optional[BriefEngine] = None


def get_brief_engine(
    db=None,
    version_manager=None,
    slack_client=None,
    sheets_client=None,
) -> BriefEngine:
    """Gibt singleton BriefEngine zurück (oder neue Instanz mit übergebenen Clients)."""
    global _engine_instance
    if db is not None or version_manager is not None:
        # Explizite Clients → immer neue Instanz (z.B. in Tests)
        return BriefEngine(
            db=db,
            version_manager=version_manager,
            slack_client=slack_client,
            sheets_client=sheets_client,
        )
    if _engine_instance is None:
        _engine_instance = BriefEngine()
    return _engine_instance
