"""
Ideen-Agent — Tier 1 der Produktionskette.
Spezifikation: agents/ideen-agent.md

Aufgabe: Rohe Ideen-Kandidaten generieren, validieren, in DB speichern
         und in Slack zur Freigabe präsentieren.

Was dieser Agent TUT:
  ✓ Claude-Haiku/Sonnet für Ideengenerierung aufrufen
  ✓ JSON-Output validieren (validate_output)
  ✓ Interne Qualitätsprüfung (self_check) — verbotene Wörter, Hook-Länge
  ✓ Ideen in DB speichern (status: IDEA_CREATED)
  ✓ Ideen in Google Sheets loggen (ERST nach IDEA_APPROVED, nicht hier)
  ✓ Ideen-Batch in Slack als Block-Kit-Karten präsentieren
  ✓ Kosten tracken (api_calls + costs Tabelle)
  ✓ Retry-Logik bei Claude-Fehlern

Was dieser Agent NICHT tut:
  ✗ Captions / Bilder / Videos generieren
  ✗ Ideen automatisch freigeben
  ✗ Ideen löschen (nur status=ARCHIVED)

Freigabe-Flow (nach diesem Modul, in slack.py / tasks.py):
  Button [FREIGEBEN] → approve_idea action → IdeenAgent.handle_approve(idea_id, user_id)
  Button [ABLEHNEN]  → reject_idea action  → IdeenAgent.handle_reject(idea_id, user_id)
  Button [DETAILS]   → show_idea_detail    → IdeenAgent.handle_detail(idea_id, user_id, channel)
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

from app.config import settings
from app.core.enums import AgentName, ContentType, JobStatus
from app.services.claude_client import ClaudeCallParams, ClaudeResponse, get_claude_client
from app.services.slack_client import get_slack_client

# TrendAgent ist optional — kein Hard-Import
try:
    from app.agents.trend_agent import TrendAgent, TrendContext
    _TREND_AGENT_AVAILABLE = True
except ImportError:
    _TREND_AGENT_AVAILABLE = False
    TrendAgent = None  # type: ignore
    TrendContext = None  # type: ignore

log = structlog.get_logger(__name__)

# ── Verbotene Wörter im Hook (Self-Check hartes Kriterium) ────────────────────
FORBIDDEN_HOOK_WORDS = frozenset([
    "kaufen", "bestellen", "jetzt", "€", "eur", "rabatt",
    "angebot", "sale", "preis", "gratis", "kostenlos", "sonderangebot",
    "% off", "discount",
])

# Potenzial → numerischer Score (für DB-Spalten click_potential etc.)
POTENZIAL_SCORE = {"hoch": 8.0, "mittel": 5.0, "niche": 3.0}

# Sortierung für Slack-Ausgabe (hoch zuerst)
POTENZIAL_ORDER = {"hoch": 0, "mittel": 1, "niche": 2}


# ── Datenklassen ───────────────────────────────────────────────────────────────

@dataclass
class IdeenAgentParams:
    """Eingabeparameter für einen Ideen-Agent-Run."""
    platform: str = "all"             # 'instagram' | 'facebook' | 'tiktok' | 'all'
    count: int = 10                    # gewünschte Anzahl (1–20)
    focus: str | None = None          # optionaler Themen-Fokus
    exclude_ids: list[str] = field(default_factory=list)   # zu vermeidende Ideen
    model: str = "haiku"              # 'haiku' | 'sonnet'
    test_mode: bool = False           # True = kein echter DB-/Slack-Call
    channel: str = ""                 # Slack-Channel für Output


@dataclass
class IdeenAgentResult:
    """Ergebnis eines Ideen-Agent-Runs."""
    batch_id: str
    ideas_requested: int
    ideas_generated: int
    ideas_passed_selfcheck: int
    ideas_rejected_selfcheck: int
    idea_ids: list[str]
    cost_eur: float
    model_used: str
    slack_posted: bool
    regenerated: bool = False          # True wenn Nachgenerierung nötig war
    trend_context_used: bool = False   # True wenn TrendAgent-Kontext in Prompt injiziert wurde
    error: str | None = None


# ── Exceptions ─────────────────────────────────────────────────────────────────

class IdeenValidationError(Exception):
    """Claude-Output ist nicht valide (kein JSON, falsche Struktur)."""

class IdeenAgentError(Exception):
    """Allgemeiner Fehler im Ideen-Agenten."""


# ── IdeenAgent ─────────────────────────────────────────────────────────────────

class IdeenAgent:
    """
    Ideen-Agent — erster Agent in der Produktionskette.

    Nutzung:
        agent = IdeenAgent(db=session)          # mit DB
        agent = IdeenAgent(test_mode=True)      # ohne DB (Dev/Test)
        result = agent.run(params)

    DB ist optional: wenn None oder test_mode=True → DB-Writes werden simuliert.
    """

    def __init__(
        self,
        db: Any = None,
        test_mode: bool = False,
        trend_agent: "TrendAgent | None" = None,
        use_trend_context: bool = True,
    ) -> None:
        self._db = db
        self._test_mode = test_mode or db is None
        self._claude = get_claude_client()
        self._slack = get_slack_client()

        # TrendAgent — optional, wird auto-erstellt wenn use_trend_context=True
        if trend_agent is not None:
            self._trend_agent = trend_agent
        elif use_trend_context and _TREND_AGENT_AVAILABLE:
            self._trend_agent = TrendAgent()
        else:
            self._trend_agent = None

        if self._test_mode:
            log.info("ideen_agent.test_mode", db_writes="simuliert")

        log.info(
            "ideen_agent.init",
            trend_agent=self._trend_agent is not None,
        )

    # ── Hauptmethode ───────────────────────────────────────────────────────────

    def run(self, params: IdeenAgentParams) -> IdeenAgentResult:
        """
        Führt einen vollständigen Ideen-Generierungs-Run durch.

        Ablauf:
          1. Prompt bauen
          2. Claude aufrufen (mit Retry)
          3. JSON validieren
          4. Self-Check (hartes Ausschlusskriterium)
          5. Nachgenerierung falls < 80% bestanden
          6. In DB speichern
          7. Slack-Batch-Übersicht + Ideen-Karten senden

        Returns:
            IdeenAgentResult mit vollständigem Audit-Trail
        """
        # Parameter normalisieren
        count = min(max(params.count, 1), settings.IDEEN_AGENT_MAX_COUNT)
        batch_id = f"BATCH-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"
        channel = params.channel or settings.SLACK_CHANNEL_CONTROL

        log.info(
            "ideen_agent.run.start",
            batch_id=batch_id,
            platform=params.platform,
            count=count,
            focus=params.focus,
            model=params.model,
            test_mode=self._test_mode,
        )

        total_cost = 0.0
        regenerated = False
        claude_response: ClaudeResponse | None = None
        trend_context_used = False

        try:
            # ── Schritt 0: Trend-Kontext abrufen (optional) ────────────────────
            trend_context = None
            if self._trend_agent is not None:
                try:
                    trend_context = self._trend_agent.get_trend_context(
                        platform=params.platform,
                        randomize=True,   # Variation zwischen Runs
                    )
                    trend_context_used = True
                    log.info(
                        "ideen_agent.trend_context.injected",
                        platform=params.platform,
                        hooks=len(trend_context.top_hooks),
                    )
                except Exception as trend_exc:
                    log.warning(
                        "ideen_agent.trend_context.failed",
                        error=str(trend_exc),
                    )
                    trend_context = None  # Kein Hard-Fail — weiter ohne Trend

            # ── Schritt 1+2: Prompt bauen und Claude aufrufen ──────────────────
            system_prompt, user_prompt = self._build_prompt(
                platform=params.platform,
                count=count,
                focus=params.focus,
                exclude_ids=params.exclude_ids,
                trend_context=trend_context,
            )

            claude_response = self._call_claude_with_retry(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=params.model,
            )
            total_cost += claude_response.cost_eur

            # ── Schritt 3: JSON-Output validieren ─────────────────────────────
            ideen = self._validate_and_parse(claude_response.text, expected_count=count)

            # ── Schritt 4: Self-Check ──────────────────────────────────────────
            approved, rejected = self.self_check(ideen)
            min_required = int(count * settings.IDEEN_AGENT_MIN_VALID_PCT / 100)

            # ── Schritt 5: Nachgenerierung falls nötig ─────────────────────────
            if len(approved) < min_required and not params.test_mode:
                log.warning(
                    "ideen_agent.selfcheck.too_few",
                    approved=len(approved),
                    rejected=len(rejected),
                    min_required=min_required,
                    triggering_regeneration=True,
                )
                rejection_reasons = list({r.get("rejection_reason", "") for r in rejected})
                regen_response = self._call_claude_with_retry(
                    system_prompt=system_prompt,
                    user_prompt=self._build_regeneration_prompt(count - len(approved), rejection_reasons),
                    model=params.model,
                )
                total_cost += regen_response.cost_eur
                regen_ideen = self._validate_and_parse(regen_response.text, expected_count=count - len(approved))
                regen_approved, _ = self.self_check(regen_ideen)
                approved.extend(regen_approved)
                regenerated = True

            # ── Schritt 6: In DB speichern ─────────────────────────────────────
            idea_ids = self._save_ideas(
                ideen=approved,
                batch_id=batch_id,
                model_used=claude_response.model_id,
                cost_eur=total_cost,
            )

            # ── Schritt 7: Slack-Output ────────────────────────────────────────
            slack_ok = self._post_to_slack(
                ideen=approved,
                idea_ids=idea_ids,
                batch_id=batch_id,
                channel=channel,
                model_key=claude_response.model_key,
                cost_eur=total_cost,
                platform=params.platform,
            )

            result = IdeenAgentResult(
                batch_id=batch_id,
                ideas_requested=count,
                ideas_generated=len(ideen),
                ideas_passed_selfcheck=len(approved),
                ideas_rejected_selfcheck=len(rejected),
                idea_ids=idea_ids,
                cost_eur=round(total_cost, 5),
                model_used=claude_response.model_id,
                slack_posted=slack_ok,
                regenerated=regenerated,
                trend_context_used=trend_context_used,
            )

            log.info(
                "ideen_agent.run.complete",
                batch_id=batch_id,
                ideas_passed=len(approved),
                ideas_rejected=len(rejected),
                cost_eur=round(total_cost, 5),
                slack_posted=slack_ok,
                regenerated=regenerated,
            )

            return result

        except Exception as exc:
            log.error(
                "ideen_agent.run.failed",
                batch_id=batch_id,
                error=str(exc),
                cost_so_far=round(total_cost, 5),
            )
            # Alert senden (Level 1 — nur #3dm-errors)
            try:
                self._slack.post_alert(
                    f"Ideen-Agent fehlgeschlagen (Batch: {batch_id}): {exc}",
                    level=1,
                )
            except Exception:
                pass  # Alert-Fehler nicht weiterwerfen

            return IdeenAgentResult(
                batch_id=batch_id,
                ideas_requested=count,
                ideas_generated=0,
                ideas_passed_selfcheck=0,
                ideas_rejected_selfcheck=0,
                idea_ids=[],
                cost_eur=round(total_cost, 5),
                model_used=params.model,
                slack_posted=False,
                error=str(exc),
            )

    # ── Freigabe-Handler (von Slack-Buttons aufgerufen) ────────────────────────

    def handle_approve(
        self,
        idea_id: str,
        user_id: str,
        channel: str,
        message_ts: str,
    ) -> None:
        """
        Verarbeitet Klick auf [FREIGEBEN]-Button.

        1. Idea-Status → IDEA_APPROVED
        2. Slack-Karte: Buttons durch "[✓ Freigegeben]" ersetzen
        3. Sheets-Zeile anlegen (ERST hier — nach Freigabe)
        4. Orchestrator-Trigger (TODO: Celery-Task starten)
        """
        log.info("ideen_agent.approve", idea_id=idea_id, user_id=user_id)

        # DB-Update
        self._update_idea_status(
            idea_id=idea_id,
            status=JobStatus.IDEA_APPROVED,
            extra={"approved_by": user_id, "approved_at": datetime.now(timezone.utc)},
        )

        # Slack-Karte aktualisieren: Buttons durch Status-Text ersetzen
        channel_id = self._resolve_channel(channel)
        self._slack.update_message(
            channel=channel_id,
            ts=message_ts,
            blocks=self._build_approved_card(idea_id),
        )

        # Sheets-Eintrag (erst nach Freigabe)
        self._log_to_sheets(idea_id=idea_id, status="IDEA_APPROVED", user=user_id)

        log.info("ideen_agent.approve.done", idea_id=idea_id)
        # TODO: Orchestrator-Celery-Task starten → Brief-Erstellung

    def handle_reject(
        self,
        idea_id: str,
        user_id: str,
        channel: str,
        message_ts: str,
    ) -> int:
        """
        Verarbeitet Klick auf [ABLEHNEN]-Button.

        1. Idea-Status → IDEA_REJECTED
        2. Slack-Karte: Buttons durch "[✗ Abgelehnt]" ersetzen
        3. Ephemeral: Anzahl noch offener Ideen
        4. Refill-Check: Falls < REFILL_THRESHOLD offen → Orchestrator informieren

        Returns:
            Anzahl noch offener Ideen im Batch
        """
        log.info("ideen_agent.reject", idea_id=idea_id, user_id=user_id)

        self._update_idea_status(
            idea_id=idea_id,
            status=JobStatus.IDEA_REJECTED,
            extra={"rejected_at": datetime.now(timezone.utc)},
        )

        channel_id = self._resolve_channel(channel)
        self._slack.update_message(
            channel=channel_id,
            ts=message_ts,
            blocks=self._build_rejected_card(idea_id),
        )

        # Anzahl offener Ideen ermitteln
        open_count = self._count_open_ideas()
        self._slack.post_ephemeral(
            channel=channel_id,
            user=user_id,
            text=f"Idee `{idea_id}` abgelehnt. Noch *{open_count}* offene Ideen.",
        )

        # Refill-Check
        if open_count < settings.IDEEN_AGENT_REFILL_THRESHOLD:
            log.warning(
                "ideen_agent.refill_needed",
                open_count=open_count,
                threshold=settings.IDEEN_AGENT_REFILL_THRESHOLD,
            )
            # TODO: Celery-Task → neuen Ideen-Lauf starten

        log.info("ideen_agent.reject.done", idea_id=idea_id, open_ideas=open_count)
        return open_count

    def handle_detail(
        self,
        idea_id: str,
        user_id: str,
        channel: str,
    ) -> None:
        """
        Verarbeitet Klick auf [DETAILS]-Button.
        Sendet Ephemeral mit vollständigem Ideen-Datensatz.
        """
        log.info("ideen_agent.detail", idea_id=idea_id, user_id=user_id)
        idea = self._load_idea(idea_id)

        channel_id = self._resolve_channel(channel)
        self._slack.post_ephemeral(
            channel=channel_id,
            user=user_id,
            blocks=self._build_detail_ephemeral(idea),
        )

    # ── Validierung & Self-Check ───────────────────────────────────────────────

    def _validate_and_parse(self, raw: str, expected_count: int) -> list[dict]:
        """
        Parst und validiert Claude-Output.
        Versucht JSON aus Antwort zu extrahieren falls nötig.
        """
        # Versuch 1: direkt parsen
        try:
            ideen = json.loads(raw.strip())
        except json.JSONDecodeError:
            # Versuch 2: JSON-Array aus Antwort extrahieren (Regex-Fallback)
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                try:
                    ideen = json.loads(match.group(0))
                except json.JSONDecodeError:
                    raise IdeenValidationError(
                        f"Claude-Output enthält kein valides JSON. "
                        f"Erste 200 Zeichen: {raw[:200]}"
                    )
            else:
                raise IdeenValidationError(
                    f"Kein JSON-Array in Claude-Output gefunden. "
                    f"Erste 200 Zeichen: {raw[:200]}"
                )

        if not isinstance(ideen, list):
            raise IdeenValidationError(f"Erwartet JSON-Array, erhalten: {type(ideen).__name__}")

        # Anzahl-Prüfung (Toleranz: min. 80%)
        min_count = int(expected_count * 0.8)
        if len(ideen) < min_count:
            raise IdeenValidationError(
                f"Zu wenige Ideen: {len(ideen)} von {expected_count} erwartet (min. {min_count})"
            )

        # Pflichtfelder validieren
        required = [
            "nummer", "plattform", "content_type", "hook",
            "emotion", "kernbotschaft", "warum_es_funktioniert",
            "visueller_ansatz", "zielgruppe", "potenzial",
        ]
        valid_platforms = {"instagram", "facebook", "tiktok"}
        valid_potentials = {"hoch", "mittel", "niche"}

        for i, idee in enumerate(ideen):
            for field_name in required:
                if field_name not in idee or not str(idee[field_name]).strip():
                    raise IdeenValidationError(
                        f"Idee {i+1}: Pflichtfeld '{field_name}' fehlt oder leer"
                    )

            if idee["plattform"] not in valid_platforms:
                raise IdeenValidationError(
                    f"Idee {i+1}: Ungültige Plattform '{idee['plattform']}'"
                )

            # Hook auto-trimmen (kein Fehler)
            if len(idee["hook"]) > 80:
                idee["hook"] = idee["hook"][:77] + "..."

            # Potenzial-Fallback
            if idee["potenzial"] not in valid_potentials:
                idee["potenzial"] = "mittel"

        log.info(
            "ideen_agent.validate.ok",
            count=len(ideen),
            expected=expected_count,
        )
        return ideen

    def self_check(self, ideen: list[dict]) -> tuple[list[dict], list[dict]]:
        """
        Interne Qualitätsprüfung vor Slack-Posting.

        Hartes Ausschlusskriterium (sofort verwerfen):
          - Hook enthält verbotene Wörter (Preise, CTAs, ...)
          - Hook > 80 Zeichen
          - emotion-Feld leer

        Weiche Kriterien (nur Warnung, Idee bleibt):
          - Hook klingt nach Werbung ("Entdecke...", "Erlebe...")
          - > 30% der Ideen mit potenzial=niche
          - > 2 Ideen mit identischer emotion

        Returns:
            (zugelassene_ideen, verworfene_ideen)
        """
        approved: list[dict] = []
        rejected: list[dict] = []

        for idee in ideen:
            hook_lower = idee["hook"].lower()

            # Hartes Kriterium 1: Verbotene Wörter
            found_word = next(
                (w for w in FORBIDDEN_HOOK_WORDS if w in hook_lower), None
            )
            if found_word:
                log.warning(
                    "ideen_agent.selfcheck.rejected",
                    hook=idee["hook"][:60],
                    reason="verbotenes_wort_in_hook",
                    word=found_word,
                )
                rejected.append({**idee, "rejection_reason": "verbotenes_wort_in_hook"})
                continue

            # Hartes Kriterium 2: Hook zu lang
            if len(idee["hook"]) > 80:
                rejected.append({**idee, "rejection_reason": "hook_zu_lang"})
                continue

            # Hartes Kriterium 3: Emotion leer
            if not idee["emotion"].strip():
                rejected.append({**idee, "rejection_reason": "emotion_leer"})
                continue

            approved.append(idee)

        # Weiche Kriterien — nur Warnungen
        if approved:
            niche_pct = sum(1 for i in approved if i["potenzial"] == "niche") / len(approved)
            if niche_pct > 0.3:
                log.warning(
                    "ideen_agent.selfcheck.many_niche",
                    niche_pct=round(niche_pct * 100),
                )

            emotion_counts: dict[str, int] = {}
            for i in approved:
                e = i["emotion"].lower()
                emotion_counts[e] = emotion_counts.get(e, 0) + 1
            for emotion, cnt in emotion_counts.items():
                if cnt > 2:
                    log.warning(
                        "ideen_agent.selfcheck.duplicate_emotion",
                        emotion=emotion,
                        count=cnt,
                    )

            adv_hooks = [
                i for i in approved
                if re.match(r"^(entdecke|erlebe|jetzt entdecken|jetzt erleben)", i["hook"].lower())
            ]
            if adv_hooks:
                log.warning(
                    "ideen_agent.selfcheck.werbung_hook",
                    count=len(adv_hooks),
                    hooks=[h["hook"][:40] for h in adv_hooks],
                )

        log.info(
            "ideen_agent.selfcheck.result",
            approved=len(approved),
            rejected=len(rejected),
        )
        return approved, rejected

    # ── Prompt-Builder ─────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        platform: str,
        count: int,
        focus: str | None,
        exclude_ids: list[str],
        trend_context: "TrendContext | None" = None,
    ) -> tuple[str, str]:
        """Baut System- und User-Prompt aus der Spezifikation."""

        # Plattform-Verteilung bei 'all'
        if platform == "all":
            ig_count = count // 2
            fb_count = count - ig_count
            plattform_info = f"Instagram: {ig_count} Ideen\nFacebook: {fb_count} Ideen"
            plattform_anweisung = (
                f"Verteile {count} Ideen gleichmäßig auf Instagram und Facebook. "
                f"Instagram-Ideen zuerst, dann Facebook-Ideen. "
                f"TikTok ist in Phase 1 noch nicht aktiv."
            )
        else:
            plattform_info = f"Plattform: {platform.title()}"
            plattform_anweisung = f"Alle {count} Ideen für {platform.title()}."

        # Ausschluss-Liste
        exclude_text = (
            "\n".join(f"- {eid}" for eid in exclude_ids[:settings.IDEEN_AGENT_EXCLUDE_LIMIT])
            if exclude_ids
            else "(keine)"
        )

        # Trend-Addendum vorbereiten (leer wenn kein Kontext vorhanden)
        trend_addendum = (
            "\n\n" + trend_context.to_prompt_addendum()
            if trend_context is not None
            else ""
        )

        system_prompt = f"""Du bist der Ideen-Agent für 3D Memories — eine Manufaktur, die Kinderzeichnungen und Haustiere in handgefertigte 3D-Figuren verwandelt.

DEINE AUFGABE:
Generiere exakt {count} Social-Media-Ideen für {platform.upper()}.
Jede Idee ist ein Ausgangspunkt für einen einzelnen Post oder ein Reel.
{plattform_anweisung}

MARKE — 3D MEMORIES:
Das Produkt ist nie der Held. Das Produkt ist das Mittel.
Der Held ist immer: der Moment, die Erinnerung, das Gefühl.
Niemals: Produktwerbung, Preise, Rabatte, CTAs wie "Jetzt kaufen".

CONTENT-PHILOSOPHIE:
Emotion → Transformation → Erinnerung → (dann erst) Produkt.
Kinderzeichnung wird real. Haustier wird unsterblich. Moment wird greifbar.
Jeder Post muss einen echten menschlichen Moment ansprechen.

VERBOTENE THEMEN (nie verwenden):
- Generische Elternratgeber-Inhalte
- Direkte Preisnennung oder Kaufaufforderung
- Vergleiche mit Wettbewerbern
- Klassische Werbeclips / "Buy now"-Energie
- Konstruierte Familienidyllen ohne Ecken und Kanten

PLATTFORM-REGELN:
Instagram → emotional, cineastisch, authentisch; Reels bevorzugt
Facebook  → familienorientiert, Nostalgie, etwas ruhiger im Ton

HOOK-PSYCHOLOGIE:
Ein guter Hook erzeugt sofortige emotionale Reaktion.
Typen: Schock | Geheimnis | Versprechen | Provokation | Nostalgie | Widerspruch
Niemals: "In diesem Post zeige ich euch..."
Hook-Länge: maximal 80 Zeichen.

AUSGABE-FORMAT:
Antworte ausschließlich mit einem JSON-Array.
Kein Fließtext, keine Erklärungen, kein Markdown außerhalb des JSON.

FORMAT JE IDEE:
{{
  "nummer":              1,
  "plattform":           "instagram",
  "content_type":        "reel",
  "hook":                "Hook-Text (max. 80 Zeichen)",
  "emotion":             "dominante Emotion (ein Wort)",
  "kernbotschaft":       "Was bleibt nach diesem Post hängen? (1 Satz)",
  "warum_es_funktioniert": "Psychologische Begründung (1–2 Sätze)",
  "visueller_ansatz":    "Was ist zu sehen? Kamera, Perspektive, Moment (2–3 Sätze)",
  "zielgruppe":          "Wen trifft das genau? (1 Satz)",
  "potenzial":           "hoch | mittel | niche"
}}

BEREITS VERWENDETE IDEEN-IDs (nicht wiederholen, ähnliche Hooks vermeiden):
{exclude_text}{trend_addendum}"""

        user_prompt = f"Generiere jetzt {count} Ideen für {platform.upper()}."

        if focus:
            user_prompt += f"\n\nTHEMEN-FOKUS FÜR DIESEN BATCH: {focus}\nAlle Ideen müssen zu diesem Thema passen. Erzwinge keine Verbindung — wenn eine Idee nicht organisch passt, wähle eine andere aus dem Themenbereich."

        return system_prompt, user_prompt

    def _build_regeneration_prompt(
        self, needed: int, rejection_reasons: list[str]
    ) -> str:
        """User-Prompt für Nachgenerierungs-Call mit Hinweis auf Ablehnungsgründe."""
        reasons_text = ", ".join(rejection_reasons) if rejection_reasons else "unbekannt"
        return (
            f"Einige deiner vorherigen Ideen wurden abgelehnt wegen: {reasons_text}.\n"
            f"Generiere jetzt {needed} NEUE Ideen die diese Probleme vermeiden.\n"
            f"Achte besonders darauf: keine Kaufaufforderungen, kein Werbeton, Hook max. 80 Zeichen."
        )

    # ── DB-Operationen ─────────────────────────────────────────────────────────

    def _save_ideas(
        self,
        ideen: list[dict],
        batch_id: str,
        model_used: str,
        cost_eur: float,
    ) -> list[str]:
        """
        Speichert Ideen in der Datenbank.
        Im Test-Mode: generiert Dummy-IDs, kein echter DB-Write.

        Returns:
            Liste der gespeicherten idea_ids (in gleicher Reihenfolge wie ideen)
        """
        year = datetime.now(timezone.utc).year
        idea_ids: list[str] = []

        for i, idee in enumerate(ideen):
            idea_id = self._generate_idea_id(year=year, idx=i + 1)
            idea_ids.append(idea_id)

            score = POTENZIAL_SCORE.get(idee.get("potenzial", "mittel"), 5.0)
            content_type_val = self._map_content_type(idee.get("content_type", "reel"))

            if self._test_mode:
                log.info(
                    "ideen_agent.db_insert.simulated",
                    idea_id=idea_id,
                    platform=idee["plattform"],
                    hook=idee["hook"][:60],
                    status=JobStatus.IDEA_CREATED,
                    batch_id=batch_id,
                )
                continue

            # Echter DB-Insert
            try:
                from app.models.idea import Idea  # type: ignore

                now = datetime.now(timezone.utc)
                obj = Idea(
                    idea_id=idea_id,
                    title=idee["kernbotschaft"][:255],
                    hook=idee["hook"],
                    core_emotion=idee["emotion"],
                    target_group=idee["zielgruppe"],
                    best_platforms=idee["plattform"],
                    content_type=content_type_val,
                    click_potential=score,
                    watchtime_potential=score,
                    share_potential=score * 0.8,
                    main_scene=idee.get("visueller_ansatz", ""),
                    transformation=idee.get("warum_es_funktioniert", ""),
                    short_description=idee.get("kernbotschaft", ""),
                    status=JobStatus.IDEA_CREATED,
                    created_by=AgentName.IDEEN_AGENT,
                    notes=f"batch_id:{batch_id} model:{model_used}",
                    created_at=now,
                )
                self._db.add(obj)
                self._db.flush()

                log.info(
                    "ideen_agent.db_insert.ok",
                    idea_id=idea_id,
                    platform=idee["plattform"],
                )

            except Exception as exc:
                log.error(
                    "ideen_agent.db_insert.error",
                    idea_id=idea_id,
                    error=str(exc),
                )
                raise

        if not self._test_mode and self._db:
            self._db.commit()
            log.info("ideen_agent.db_commit.ok", count=len(idea_ids))

        return idea_ids

    def _update_idea_status(
        self,
        idea_id: str,
        status: JobStatus,
        extra: dict | None = None,
    ) -> None:
        """Aktualisiert den Status einer Idee in der DB."""
        if self._test_mode:
            log.info(
                "ideen_agent.db_update.simulated",
                idea_id=idea_id,
                status=status,
                extra=extra or {},
            )
            return

        try:
            from app.models.idea import Idea  # type: ignore
            obj = self._db.query(Idea).filter(Idea.idea_id == idea_id).first()
            if not obj:
                log.error("ideen_agent.db_update.not_found", idea_id=idea_id)
                return
            obj.status = status
            if extra:
                for k, v in extra.items():
                    if hasattr(obj, k):
                        setattr(obj, k, v)
            self._db.commit()
        except Exception as exc:
            log.error("ideen_agent.db_update.error", idea_id=idea_id, error=str(exc))
            raise

    def _count_open_ideas(self) -> int:
        """Zählt Ideen mit status=IDEA_CREATED (noch nicht entschieden)."""
        if self._test_mode:
            return 5  # Dummy-Wert für Tests

        try:
            from app.models.idea import Idea  # type: ignore
            return (
                self._db.query(Idea)
                .filter(Idea.status == JobStatus.IDEA_CREATED, Idea.is_deleted == False)
                .count()
            )
        except Exception:
            return 0

    def _load_idea(self, idea_id: str) -> dict:
        """Lädt eine Idee aus der DB als Dict."""
        if self._test_mode:
            return {
                "idea_id": idea_id,
                "hook": "Dummy-Hook für Test",
                "emotion": "Nostalgie",
                "plattform": "instagram",
                "content_type": "reel",
                "kernbotschaft": "Test-Kernbotschaft",
                "warum_es_funktioniert": "Test-Begründung",
                "visueller_ansatz": "Test-Visuell",
                "zielgruppe": "Test-Zielgruppe",
                "potenzial": "hoch",
                "status": JobStatus.IDEA_CREATED,
            }

        try:
            from app.models.idea import Idea  # type: ignore
            obj = self._db.query(Idea).filter(Idea.idea_id == idea_id).first()
            if not obj:
                return {"idea_id": idea_id, "error": "not_found"}
            return {
                "idea_id": obj.idea_id,
                "hook": obj.hook,
                "emotion": obj.core_emotion,
                "kernbotschaft": obj.short_description or "",
                "visueller_ansatz": obj.main_scene or "",
                "zielgruppe": obj.target_group,
                "status": obj.status,
                "created_at": str(obj.created_at),
                "notes": obj.notes or "",
            }
        except Exception as exc:
            log.error("ideen_agent.load_idea.error", idea_id=idea_id, error=str(exc))
            return {"idea_id": idea_id, "error": str(exc)}

    # ── Sheets-Logging ─────────────────────────────────────────────────────────

    def _log_to_sheets(self, idea_id: str, status: str, user: str) -> None:
        """
        Loggt eine Idee in Google Sheets — ERST nach IDEA_APPROVED.
        Fehler werden geloggt aber nicht weitergeworfen (non-blocking).
        """
        if self._test_mode:
            log.info(
                "ideen_agent.sheets_log.simulated",
                idea_id=idea_id,
                status=status,
            )
            return

        try:
            from app.services.sheets_client import get_sheets_client
            sc = get_sheets_client()
            sc.ensure_header()
            sc.append_job_row({
                "content_id": idea_id,
                "status": status,
                "approved_by": user,
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as exc:
            # Sheets-Fehler stoppen NICHT den Job — nur loggen
            log.error(
                "ideen_agent.sheets_log.error",
                idea_id=idea_id,
                error=str(exc),
            )

    # ── Slack Block Kit ────────────────────────────────────────────────────────

    def _post_to_slack(
        self,
        ideen: list[dict],
        idea_ids: list[str],
        batch_id: str,
        channel: str,
        model_key: str,
        cost_eur: float,
        platform: str,
    ) -> bool:
        """
        Sendet Batch-Übersicht + sortierte Ideen-Karten an Slack.

        Reihenfolge: potenzial=hoch zuerst, dann mittel, dann niche.
        Pause zwischen Nachrichten: 500ms (Rate-Limit).
        """
        try:
            channel_id = self._resolve_channel(channel)

            # 1. Batch-Übersicht
            self._slack.post_message(
                channel=channel_id,
                text=f"Neue Ideen — {len(ideen)} generiert",
                blocks=self._build_batch_overview(
                    count=len(ideen),
                    platform=platform,
                    model_key=model_key,
                    cost_eur=cost_eur,
                    batch_id=batch_id,
                ),
            )
            self._slack.pause_between_messages(500)

            # 2. Ideen-Karten sortiert nach Potenzial
            sorted_pairs = sorted(
                zip(ideen, idea_ids),
                key=lambda x: POTENZIAL_ORDER.get(x[0].get("potenzial", "mittel"), 1),
            )

            for n, (idee, idea_id) in enumerate(sorted_pairs, start=1):
                try:
                    self._slack.post_message(
                        channel=channel_id,
                        text=f"Idee {n}/{len(ideen)}: {idee['hook'][:60]}",
                        blocks=self._build_idea_card(
                            idee=idee,
                            idea_id=idea_id,
                            n=n,
                            total=len(ideen),
                        ),
                    )
                    self._slack.pause_between_messages(500)
                except Exception as exc:
                    log.error(
                        "ideen_agent.slack.card_failed",
                        idea_id=idea_id,
                        n=n,
                        error=str(exc),
                    )

            # 3. Abschluss-Nachricht
            self._slack.post_message(
                channel=channel_id,
                text=(
                    f"*{len(ideen)} Ideen* warten auf deine Entscheidung. "
                    f"Nicht freigegebene Ideen verfallen nach {settings.IDEEN_AGENT_IDEA_TTL_DAYS} Tagen."
                ),
            )

            log.info(
                "ideen_agent.slack.posted",
                channel=channel_id,
                count=len(ideen),
            )
            return True

        except Exception as exc:
            log.error("ideen_agent.slack.post_failed", error=str(exc))
            return False

    def _build_batch_overview(
        self,
        count: int,
        platform: str,
        model_key: str,
        cost_eur: float,
        batch_id: str,
    ) -> list[dict]:
        """Block-Kit-Blöcke für die Batch-Übersichts-Nachricht."""
        date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"NEUE IDEEN — {date_str}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Anzahl:*\n{count} Ideen"},
                    {"type": "mrkdwn", "text": f"*Plattform:*\n{platform.title()}"},
                    {"type": "mrkdwn", "text": f"*Modell:*\nClaude {model_key.title()}"},
                    {"type": "mrkdwn", "text": f"*Kosten:*\n{cost_eur:.4f} EUR"},
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Batch-ID: `{batch_id}` · Wähle Ideen zur Weiterverarbeitung.",
                    }
                ],
            },
            {"type": "divider"},
        ]

    def _build_idea_card(
        self,
        idee: dict,
        idea_id: str,
        n: int,
        total: int,
    ) -> list[dict]:
        """Block-Kit-Blöcke für eine einzelne Ideen-Karte mit Buttons."""
        potenzial_emoji = {"hoch": ":star:", "mittel": ":large_blue_circle:", "niche": ":white_circle:"}.get(
            idee.get("potenzial", "mittel"), ":white_circle:"
        )

        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{n}/{total} — {idee['plattform'].title()} · {idee.get('content_type', 'reel').title()}*\n\n"
                        f"*Hook:*\n_{idee['hook']}_"
                    ),
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Emotion:*\n{idee['emotion']}"},
                    {"type": "mrkdwn", "text": f"*Potenzial:*\n{potenzial_emoji} {idee.get('potenzial', '?').title()}"},
                    {"type": "mrkdwn", "text": f"*Zielgruppe:*\n{idee['zielgruppe'][:80]}"},
                    {"type": "mrkdwn", "text": f"*Idee-ID:*\n`{idea_id}`"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Kernbotschaft:*\n{idee['kernbotschaft']}\n\n"
                        f"*Warum es funktioniert:*\n{idee['warum_es_funktioniert']}"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Visueller Ansatz:*\n{idee['visueller_ansatz']}",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "FREIGEBEN"},
                        "style": "primary",
                        "action_id": "approve_idea",
                        "value": json.dumps({"idea_id": idea_id}),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "ABLEHNEN"},
                        "style": "danger",
                        "action_id": "reject_idea",
                        "value": json.dumps({"idea_id": idea_id}),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "DETAILS"},
                        "action_id": "show_idea_detail",
                        "value": json.dumps({"idea_id": idea_id}),
                    },
                ],
            },
            {"type": "divider"},
        ]

    def _build_approved_card(self, idea_id: str) -> list[dict]:
        """Ersetzt die Ideen-Karte nach Freigabe (ohne Buttons)."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":white_check_mark: *Idee `{idea_id}` freigegeben.*\nWird zur Content-Produktion weitergeleitet.",
                },
            }
        ]

    def _build_rejected_card(self, idea_id: str) -> list[dict]:
        """Ersetzt die Ideen-Karte nach Ablehnung (ohne Buttons)."""
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f":x: *Idee `{idea_id}` abgelehnt.*",
                },
            }
        ]

    def _build_detail_ephemeral(self, idea: dict) -> list[dict]:
        """Block-Kit für den Detail-Ephemeral (alle Felder ausgeklappt)."""
        fields = []
        for k, v in idea.items():
            if k not in ("error",) and v:
                fields.append({"type": "mrkdwn", "text": f"*{k}:*\n{str(v)[:200]}"})

        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Details: {idea.get('idea_id', '?')}"},
            },
            {
                "type": "section",
                "fields": fields[:10],  # Block-Kit-Limit: max. 10 fields
            },
        ]

    # ── Hilfsmethoden ──────────────────────────────────────────────────────────

    def _call_claude_with_retry(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
    ) -> ClaudeResponse:
        """Claude-Call mit max. 2 Retries und 90s Backoff bei Fehlern."""
        from tenacity import retry, stop_after_attempt, wait_fixed
        from app.services.claude_client import ClaudeRateLimitError

        @retry(
            retry=retry_if_exception_type_safe(ClaudeRateLimitError),
            stop=stop_after_attempt(3),
            wait=wait_fixed(30),
            reraise=True,
        )
        def _do():
            return self._claude.complete(ClaudeCallParams(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model,
                max_tokens=3000,
            ))

        return _do()

    def _generate_idea_id(self, year: int, idx: int) -> str:
        """
        Generiert eine Ideen-ID im Format 3DM-YYYY-NNN.
        Im Test-Mode: deterministisch. In Production: DB-Sequenz.
        """
        if self._test_mode:
            return f"3DM-{year}-TEST-{idx:03d}"

        # Production: letzten NNN-Wert aus DB lesen + 1
        try:
            from app.models.idea import Idea  # type: ignore
            last = (
                self._db.query(Idea.idea_id)
                .filter(Idea.idea_id.like(f"3DM-{year}-%"))
                .order_by(Idea.idea_id.desc())
                .first()
            )
            if last and last[0]:
                # Format: 3DM-2026-042 → 042
                parts = last[0].split("-")
                last_num = int(parts[-1]) if parts[-1].isdigit() else 0
            else:
                last_num = 0
            return f"3DM-{year}-{last_num + idx:03d}"
        except Exception:
            # Fallback: UUID-basiert
            return f"3DM-{year}-{uuid.uuid4().hex[:6].upper()}"

    @staticmethod
    def _map_content_type(ct: str) -> str:
        """Normalisiert content_type auf Enum-Werte."""
        mapping = {
            "reel": ContentType.REEL,
            "carousel": ContentType.CAROUSEL,
            "image": ContentType.IMAGE,
            "single": ContentType.IMAGE,
            "story": ContentType.STORY,
            "short": ContentType.SHORT,
            "video": ContentType.VIDEO,
        }
        return mapping.get(ct.lower(), ContentType.IMAGE)

    @staticmethod
    def _resolve_channel(channel: str) -> str:
        """Gibt Channel-ID zurück; fallback auf SLACK_CHANNEL_CONTROL."""
        return channel or settings.SLACK_CHANNEL_CONTROL


# ── Hilfsfunktion für retry_if_exception_type_safe ────────────────────────────

def retry_if_exception_type_safe(exc_type):
    """tenacity retry-Prädikat — sicher importierbar."""
    from tenacity import retry_if_exception_type
    return retry_if_exception_type(exc_type)
