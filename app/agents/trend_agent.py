"""
Trend-Agent — Unterstützungssystem für den Ideen-Agenten.

Phase 1: Simulierte Trenddaten aus manuell gepflegter Bibliothek.
Phase 2 (geplant): Web-Scraping echter Plattform-Daten.

Aufgaben:
  ✓ Hook-Strukturen analysieren und optimieren
  ✓ Watchtime-Muster auswählen
  ✓ Meme-Strukturen empfehlen
  ✓ Transformationstrends priorisieren
  ✓ Trend-Kontext als Prompt-Fragment für IdeenAgent generieren

Was dieser Agent NICHT tut:
  ✗ Keine echten Websuchen (Phase 1)
  ✗ Keine Plattform-API-Calls
  ✗ Keine Bewertung einzelner Ideen — das macht der Scoring-Agent
  ✗ Keine eigenständigen DB-Writes

Nutzung:
    agent = TrendAgent()
    ctx = agent.get_trend_context(platform="instagram")
    # → ctx.to_prompt_addendum() liefert Text für System-Prompt
    # → ctx.top_hooks liefert sortierte Hook-Empfehlungen
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

import structlog

from app.data.trend_library import (
    HOOK_PATTERNS,
    MEME_STRUCTURES,
    PLATFORM_TRENDS,
    TRANSFORMATION_TRENDS,
    WATCHTIME_PATTERNS,
    HookPattern,
    MemeStructure,
    PlatformTrend,
    TransformationTrend,
    WatchtimePattern,
    get_hooks_by_platform,
    get_platform_trend,
    get_top_hooks,
    get_top_transformations,
    get_watchtime_patterns_for_platform,
)

log = structlog.get_logger(__name__)


# ── Ausgabe-Datenklassen ───────────────────────────────────────────────────────

@dataclass
class HookAnalysis:
    """
    Analyse eines einzelnen Hooks aus einer Idee.
    Wird von analyze_hook_strength() zurückgegeben.
    """
    original_hook: str
    detected_pattern: str | None           # Name des erkannten HookPattern (oder None)
    detected_category: str | None          # Kategorie des erkannten Patterns
    estimated_virality: float              # 0–10
    emotion_match: bool                    # True wenn Hook-Emotion zur Idee-Emotion passt
    issues: list[str]                      # Gefundene Schwachstellen
    suggestions: list[str]                 # Konkrete Verbesserungsvorschläge
    alternative_templates: list[str]       # 2–3 alternative Hook-Formulierungen


@dataclass
class ViralityAssessment:
    """
    Viralitäts-Einschätzung für eine Idee.
    Berücksichtigt Hook, Format, Plattform und Transformationstyp.
    """
    idea_hook: str
    platform: str
    content_type: str
    virality_score: float                  # 0–10 (Einschätzung durch Bibliotheksvergleich)
    matching_meme_structure: str | None    # Passendes Meme-Format falls erkennbar
    matching_transformation: str | None    # Passendes Transformationsmuster
    format_recommendation: str            # Empfohlenes Content-Format
    reasoning: str                         # Kurze Begründung


@dataclass
class TrendContext:
    """
    Vollständiger Trend-Kontext für einen IdeenAgent-Run.
    Wird in den System-Prompt injiziert.
    """
    platform: str
    top_hooks: list[HookPattern]
    top_watchtime_patterns: list[WatchtimePattern]
    top_meme_structures: list[MemeStructure]
    top_transformations: list[TransformationTrend]
    platform_trend: PlatformTrend | None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    library_version: str = "1.0.0"

    def to_prompt_addendum(self) -> str:
        """
        Gibt einen kompakten Prompt-Text zurück, der in den System-Prompt
        des IdeenAgenten injiziert werden kann.

        Bewusst kompakt gehalten — Claude braucht Orientierung, keine Vorschriften.
        """
        lines: list[str] = [
            "─────────────────────────────────────────────────────────────────",
            "TREND-KONTEXT (basierend auf Bibliothek v" + self.library_version + "):",
            "",
        ]

        # Top-Hook-Muster
        lines.append("TOP-HOOK-STRUKTUREN (nutze diese als Inspiration — nicht wörtlich kopieren):")
        for h in self.top_hooks[:5]:
            lines.append(f"  • [{h.category.upper()}] Vorlage: {h.template}")
            lines.append(f"    Beispiel: \"{h.example}\"")
            lines.append(f"    Emotion: {h.emotion} | Virality: {h.virality_score}/10")
        lines.append("")

        # Top-Watchtime-Patterns
        lines.append("EMPFOHLENE WATCHTIME-STRUKTUREN:")
        for w in self.top_watchtime_patterns[:3]:
            lines.append(f"  • {w.name} ({w.ideal_length_sec}s) — {w.example}")
        lines.append("")

        # Transformations-Trends
        lines.append("STÄRKSTE TRANSFORMATIONS-NARRATIVE:")
        for t in self.top_transformations[:3]:
            lines.append(f"  • {t.name}: \"{t.example_hook}\"")
            lines.append(f"    Emotion: {t.emotional_core}")
        lines.append("")

        # Meme-Strukturen
        lines.append("AKTUELLE MEME-/FORMAT-STRUKTUREN:")
        for m in self.top_meme_structures[:3]:
            lines.append(f"  • {m.name}: {m.format_template}")
        lines.append("")

        # Plattform-spezifische Hinweise
        if self.platform_trend:
            pt = self.platform_trend
            lines.append(f"PLATTFORM-HINWEISE ({self.platform.upper()}):")
            lines.append(f"  Ton: {pt.tone[:150]}")
            lines.append(f"  Trending-Themen: {', '.join(pt.trending_topics[:4])}")
            lines.append(f"  Vermeiden: {', '.join(pt.avoid[:3])}")
            lines.append("")

        lines.append(
            "ANWEISUNG: Diese Trends sind Inspiration — keine Vorschrift. "
            "Originelle Ideen, die Trends brechen, sind willkommen solange "
            "die emotionale Wirkung stark bleibt."
        )
        lines.append("─────────────────────────────────────────────────────────────────")

        return "\n".join(lines)


# ── TrendAgent ─────────────────────────────────────────────────────────────────

class TrendAgent:
    """
    Trend-Agent — liefert Trend-Kontext und Analyse-Funktionen.

    Ist zustandslos und benötigt keine DB-Verbindung.
    Alle Methoden können im Test- und Production-Modus identisch genutzt werden.

    Nutzung:
        agent = TrendAgent()

        # Kontext für IdeenAgent-Prompt:
        ctx = agent.get_trend_context("instagram")
        prompt_addendum = ctx.to_prompt_addendum()

        # Einzelne Hook-Analyse:
        analysis = agent.analyze_hook_strength("War dieser Moment nicht zu kostbar?")

        # Viralitäts-Einschätzung:
        virality = agent.assess_virality(
            hook="Sie hat nie aufgehört zu lachen.",
            platform="instagram",
            content_type="reel",
            emotion="Freude",
        )
    """

    def __init__(self) -> None:
        log.info("trend_agent.init", library_version="1.0.0", mode="simulated")

    # ── Haupt-Methode ──────────────────────────────────────────────────────────

    def get_trend_context(
        self,
        platform: str = "all",
        top_n_hooks: int = 5,
        top_n_watchtime: int = 3,
        top_n_memes: int = 3,
        top_n_transformations: int = 3,
        randomize: bool = False,
    ) -> TrendContext:
        """
        Gibt einen vollständigen TrendContext für den angegebenen Platform-Kontext zurück.

        Args:
            platform:             "instagram" | "facebook" | "all"
            top_n_hooks:          Anzahl der zurückgegebenen Hook-Muster
            top_n_watchtime:      Anzahl der zurückgegebenen Watchtime-Muster
            top_n_memes:          Anzahl der zurückgegebenen Meme-Strukturen
            top_n_transformations: Anzahl der zurückgegebenen Transformations-Trends
            randomize:            Wenn True, werden zufällige Muster aus den Top-Einträgen
                                  gewählt (sorgt für Variation zwischen Runs)

        Returns:
            TrendContext — enthält alle Daten + .to_prompt_addendum() für Prompt-Injektion
        """
        log.info(
            "trend_agent.context.start",
            platform=platform,
            top_n_hooks=top_n_hooks,
            randomize=randomize,
        )

        # Hook-Patterns: plattform-gefiltert wenn möglich
        if platform == "all":
            hooks = sorted(HOOK_PATTERNS, key=lambda h: h.virality_score, reverse=True)
        else:
            hooks = sorted(
                get_hooks_by_platform(platform),
                key=lambda h: h.virality_score,
                reverse=True,
            )
            # Fallback auf alle wenn Plattform-Filter zu wenige liefert
            if len(hooks) < top_n_hooks:
                hooks = sorted(HOOK_PATTERNS, key=lambda h: h.virality_score, reverse=True)

        # Watchtime-Patterns: plattform-gefiltert
        if platform == "all":
            watchtime = WATCHTIME_PATTERNS
        else:
            watchtime = get_watchtime_patterns_for_platform(platform)
            if len(watchtime) < top_n_watchtime:
                watchtime = list(WATCHTIME_PATTERNS)

        # Meme-Strukturen: alle (plattform-unabhängig)
        memes = sorted(
            MEME_STRUCTURES,
            key=lambda m: {"high": 3, "medium": 2, "niche": 1}.get(m.viral_ceiling, 0),
            reverse=True,
        )

        # Transformations-Trends: nach Hook-Potenzial sortiert
        transformations = sorted(
            TRANSFORMATION_TRENDS,
            key=lambda t: t.hook_potential,
            reverse=True,
        )

        # Platform-Trend
        platform_trend = get_platform_trend(platform)

        # Optionale Randomisierung für Variation
        if randomize:
            top_hooks = self._randomized_pick(hooks, top_n_hooks, pool_multiplier=2)
            top_watchtime = self._randomized_pick(watchtime, top_n_watchtime, pool_multiplier=2)
            top_memes = self._randomized_pick(memes, top_n_memes, pool_multiplier=2)
            top_transformations = self._randomized_pick(
                transformations, top_n_transformations, pool_multiplier=2
            )
        else:
            top_hooks = list(hooks[:top_n_hooks])
            top_watchtime = list(watchtime[:top_n_watchtime])
            top_memes = list(memes[:top_n_memes])
            top_transformations = list(transformations[:top_n_transformations])

        ctx = TrendContext(
            platform=platform,
            top_hooks=top_hooks,
            top_watchtime_patterns=top_watchtime,
            top_meme_structures=top_memes,
            top_transformations=top_transformations,
            platform_trend=platform_trend,
        )

        log.info(
            "trend_agent.context.done",
            platform=platform,
            hooks=len(top_hooks),
            watchtime=len(top_watchtime),
            memes=len(top_memes),
            transformations=len(top_transformations),
        )

        return ctx

    # ── Analyse-Methoden ───────────────────────────────────────────────────────

    def analyze_hook_strength(
        self,
        hook: str,
        platform: str = "instagram",
        idea_emotion: str = "",
    ) -> HookAnalysis:
        """
        Analysiert die Stärke eines gegebenen Hooks.

        Vergleicht den Hook mit der Bibliothek anhand von:
        - Schlüsselwörtern und Muster-Erkennung
        - Länge und Lesbarkeit
        - Emotions-Konsistenz
        - Virality-Einschätzung durch Muster-Match

        Returns:
            HookAnalysis mit Bewertung, Problemen und Alternativen
        """
        hook_lower = hook.lower()
        issues: list[str] = []
        suggestions: list[str] = []

        # ── Längen-Check ───────────────────────────────────────────────────────
        if len(hook) > 80:
            issues.append(f"Hook zu lang ({len(hook)} Zeichen, max. 80)")
            suggestions.append("Kürzen auf den emotionalen Kern — alles streichen was nicht trifft")

        if len(hook) < 20:
            issues.append("Hook zu kurz — kein emotionaler Sog aufgebaut")
            suggestions.append("Kontext oder Tension ergänzen")

        # ── Verbotene Signalwörter ─────────────────────────────────────────────
        advert_words = ["kaufen", "bestellen", "rabatt", "angebot", "preis", "€", "eur",
                        "gratis", "kostenlos", "sale", "discount"]
        found_advert = [w for w in advert_words if w in hook_lower]
        if found_advert:
            issues.append(f"Werbewörter gefunden: {', '.join(found_advert)}")
            suggestions.append("Kaufaufforderungen aus dem Hook entfernen — Emotion zuerst, Produkt nie")

        # ── Cliché-Erkennung ──────────────────────────────────────────────────
        clichees = ["in diesem video", "heute zeige ich", "willkommen bei", "hey leute"]
        found_clichee = next((c for c in clichees if c in hook_lower), None)
        if found_clichee:
            issues.append(f"Klischee-Eröffnung erkannt: '{found_clichee}'")
            suggestions.append("Mit dem emotionalen Kern beginnen, nicht mit Ankündigung")

        # ── Frage-Analyse ──────────────────────────────────────────────────────
        has_question = "?" in hook
        has_direct_address = any(w in hook_lower for w in ["du", "dein", "deine", "dir"])

        if not has_question and not has_direct_address:
            suggestions.append(
                "Erwäge eine direkte Ansprache (du/dein) oder eine Frage "
                "— erhöht Einbezug und Kommentar-Bereitschaft"
            )

        # ── Pattern-Matching ──────────────────────────────────────────────────
        detected_pattern, detected_category, pattern_virality = self._match_hook_pattern(hook_lower)

        # Estimated Virality
        estimated_virality = pattern_virality if detected_pattern else self._estimate_virality(hook)

        # Emotions-Match
        emotion_match = True
        if idea_emotion and detected_pattern:
            matching_pattern = next(
                (p for p in HOOK_PATTERNS if p.name == detected_pattern), None
            )
            if matching_pattern and matching_pattern.emotion.lower() != idea_emotion.lower():
                emotion_match = False
                suggestions.append(
                    f"Hook-Emotion ({matching_pattern.emotion}) passt nicht zur Idee-Emotion ({idea_emotion}). "
                    f"Erwäge ein anderes Hook-Pattern."
                )

        # ── Alternative Hooks ─────────────────────────────────────────────────
        platform_hooks = get_hooks_by_platform(platform)
        top_platform = sorted(platform_hooks, key=lambda h: h.virality_score, reverse=True)[:3]
        alternative_templates = [
            f"[{h.category.upper()}] {h.template} — z.B.: \"{h.example}\""
            for h in top_platform
            if h.name != detected_pattern
        ]

        log.info(
            "trend_agent.hook_analysis.done",
            hook_length=len(hook),
            pattern=detected_pattern,
            virality=estimated_virality,
            issues=len(issues),
        )

        return HookAnalysis(
            original_hook=hook,
            detected_pattern=detected_pattern,
            detected_category=detected_category,
            estimated_virality=round(estimated_virality, 1),
            emotion_match=emotion_match,
            issues=issues,
            suggestions=suggestions,
            alternative_templates=alternative_templates[:3],
        )

    def assess_virality(
        self,
        hook: str,
        platform: str,
        content_type: str,
        emotion: str = "",
        transformation_input: str = "",
    ) -> ViralityAssessment:
        """
        Bewertet das Viralitätspotenzial einer Idee.

        Kombiniert Hook-Analyse, Format-Empfehlung und Transformationsmuster
        zu einer Gesamteinschätzung.

        Args:
            hook:                  Der Ideen-Hook
            platform:              Zielplattform
            content_type:          Geplanter Content-Typ (reel, carousel, etc.)
            emotion:               Dominante Emotion der Idee
            transformation_input:  Optionaler Hinweis auf Transformationstyp

        Returns:
            ViralityAssessment mit Score und Empfehlungen
        """
        hook_lower = hook.lower()

        # Hook-Basis-Score
        _, _, hook_virality = self._match_hook_pattern(hook_lower)
        if hook_virality == 0:
            hook_virality = self._estimate_virality(hook)

        # Plattform-Bonus
        platform_bonus = self._get_platform_format_bonus(platform, content_type)

        # Meme-Match
        matching_meme = self._find_matching_meme(hook_lower, emotion)

        # Transformations-Match
        matching_transformation = self._find_matching_transformation(hook_lower, transformation_input)
        transformation_bonus = 0.5 if matching_transformation else 0.0

        # Gesamtscore
        virality_score = min(10.0, hook_virality + platform_bonus + transformation_bonus)

        # Format-Empfehlung
        pt = get_platform_trend(platform)
        recommended_format = pt.format_priority[0] if pt else content_type

        # Begründung
        reasoning_parts = []
        if matching_transformation:
            reasoning_parts.append(f"Transformationsmuster '{matching_transformation}' erkannt")
        if matching_meme:
            reasoning_parts.append(f"Passt zu Meme-Struktur '{matching_meme}'")
        if platform_bonus > 0:
            reasoning_parts.append(
                f"Format '{content_type}' gut für {platform} (+{platform_bonus:.1f})"
            )
        if not reasoning_parts:
            reasoning_parts.append("Kein dominantes Muster erkannt — Originalität bewertet")

        log.info(
            "trend_agent.virality.done",
            hook=hook[:40],
            platform=platform,
            virality_score=virality_score,
        )

        return ViralityAssessment(
            idea_hook=hook,
            platform=platform,
            content_type=content_type,
            virality_score=round(virality_score, 1),
            matching_meme_structure=matching_meme,
            matching_transformation=matching_transformation,
            format_recommendation=recommended_format,
            reasoning="; ".join(reasoning_parts),
        )

    def analyze_hook_batch(
        self,
        ideas: list[dict],
        platform: str = "instagram",
    ) -> list[dict]:
        """
        Analysiert alle Hooks in einer Ideen-Liste und reichert sie mit Trend-Daten an.

        Ergänzt jede Idee um:
          - hook_analysis:    HookAnalysis-Objekt
          - virality:         ViralityAssessment-Objekt
          - trend_enriched:   True (Marker dass Analyse durchgeführt)

        Args:
            ideas:    Liste von Ideen-Dicts (aus IdeenAgent)
            platform: Zielplattform für die Analyse

        Returns:
            Angereichertes ideas-List — original-Dicts werden nicht verändert
        """
        enriched = []
        for idee in ideas:
            hook = idee.get("hook", "")
            emotion = idee.get("emotion", "")
            content_type = idee.get("content_type", "reel")
            idea_platform = idee.get("plattform", platform)

            hook_analysis = self.analyze_hook_strength(hook, idea_platform, emotion)
            virality = self.assess_virality(hook, idea_platform, content_type, emotion)

            enriched.append({
                **idee,
                "hook_analysis": hook_analysis,
                "virality": virality,
                "trend_enriched": True,
            })

        log.info(
            "trend_agent.batch_analysis.done",
            count=len(enriched),
            platform=platform,
        )

        return enriched

    def get_hook_recommendations(
        self,
        platform: str,
        emotion: str = "",
        top_n: int = 5,
    ) -> list[HookPattern]:
        """
        Gibt Hook-Empfehlungen für eine Plattform und optionale Ziel-Emotion zurück.

        Args:
            platform:  Zielplattform
            emotion:   Gewünschte Ziel-Emotion (optional, für Filterung)
            top_n:     Anzahl der zurückgegebenen Empfehlungen

        Returns:
            Sortierte Liste von HookPattern-Objekten
        """
        hooks = get_hooks_by_platform(platform)

        if emotion:
            # Priorisiere Hooks die zur gewünschten Emotion passen
            emotion_lower = emotion.lower()
            matching = [h for h in hooks if h.emotion.lower() == emotion_lower]
            non_matching = [h for h in hooks if h.emotion.lower() != emotion_lower]
            hooks = matching + non_matching

        return sorted(hooks, key=lambda h: h.virality_score, reverse=True)[:top_n]

    def get_watchtime_recommendation(
        self,
        platform: str,
        content_type: str = "reel",
    ) -> WatchtimePattern | None:
        """
        Gibt die beste Watchtime-Empfehlung für Plattform und Content-Typ zurück.
        """
        patterns = get_watchtime_patterns_for_platform(platform)
        compatible = [p for p in patterns if content_type in p.content_types]
        if not compatible:
            compatible = patterns

        return max(compatible, key=lambda p: p.ideal_length_sec, default=None)

    # ── Interne Hilfsmethoden ──────────────────────────────────────────────────

    def _match_hook_pattern(
        self, hook_lower: str
    ) -> tuple[str | None, str | None, float]:
        """
        Versucht, ein bekanntes Hook-Pattern im gegebenen Hook zu erkennen.

        Returns:
            (pattern_name, category, virality_score) — alle None/0 wenn kein Match
        """
        # Schlüsselwort-basierte Erkennung
        PATTERN_SIGNALS: dict[str, list[str]] = {
            "Schock-Reversal":          ["nie gedacht", "hätte nie"],
            "Geheimnis-Enthüllung":     ["der grund", "weshalb", "warum"],
            "Widerspruchs-Hook":        ["alle reden", "niemand spricht", "alle sagen"],
            "Nostalgie-Trigger":        ["jahren", "damals", "früher", "vor dem", "vor einem"],
            "Transformations-Versprechen": ["bevor", "vorher", "nachher", "wurde"],
            "Einbezug-Frage":           ["weißt du", "kennst du", "erinnerst du"],
            "Konkret-Zahl":             ["jahre", "stunden", "tage", "monate"],
            "POV-Immersion":            ["pov:", "pov "],
            "Moment-Einfrieren":        ["dieser moment", "genau dieser", "dieser augenblick"],
            "Eltern-Identifikation":    ["elternteil", "mutter", "vater", "mama", "papa",
                                         "kind", "kinder"],
            "Verlust-Bewusstsein":      ["eines tages", "nicht mehr", "nicht immer",
                                         "zu kurz", "bald vorbei"],
            "Überraschungs-Reaktion":   ["niemand erwartet", "unerwartet", "erstaunen"],
            "Zukunfts-Zeitreise":       ["in 20 jahren", "in 10 jahren", "eines tages",
                                         "später werden"],
            "Universelle-Wahrheit":     ["manche dinge", "einige dinge", "zu wichtig",
                                         "zu kostbar"],
        }

        best_name = None
        best_score = 0.0
        best_category = None

        for pattern in HOOK_PATTERNS:
            signals = PATTERN_SIGNALS.get(pattern.name, [])
            if any(sig in hook_lower for sig in signals):
                if pattern.virality_score > best_score:
                    best_score = pattern.virality_score
                    best_name = pattern.name
                    best_category = pattern.category

        return best_name, best_category, best_score

    def _estimate_virality(self, hook: str) -> float:
        """
        Schätzt Virality für Hooks ohne erkanntes Muster.
        Basiert auf einfachen Heuristiken.
        """
        score = 6.0  # Basisscore für unbekannte Hooks

        hook_lower = hook.lower()

        # Positive Signale
        if "?" in hook:
            score += 0.5
        if any(w in hook_lower for w in ["du", "dein", "deine"]):
            score += 0.4
        if len(hook) < 60:
            score += 0.3  # Kürze ist Stärke
        if any(w in hook_lower for w in ["nie", "nie mehr", "immer"]):
            score += 0.3
        if any(w in hook_lower for w in ["kind", "familie", "mutter", "vater"]):
            score += 0.4

        # Negative Signale
        if len(hook) > 70:
            score -= 0.3
        if hook_lower.startswith(("in diesem", "heute zeige", "willkommen")):
            score -= 1.0

        return round(min(10.0, max(0.0, score)), 1)

    def _get_platform_format_bonus(self, platform: str, content_type: str) -> float:
        """Gibt einen kleinen Bonus für optimal kombinierte Plattform+Format-Paare."""
        optimal = {
            "instagram": {"reel": 0.5, "carousel": 0.3, "story": 0.1},
            "facebook":  {"video": 0.5, "reel": 0.3, "image": 0.2},
            "tiktok":    {"short": 0.5, "reel": 0.4},
        }
        return optimal.get(platform, {}).get(content_type, 0.0)

    def _find_matching_meme(self, hook_lower: str, emotion: str) -> str | None:
        """Erkennt welche Meme-Struktur zu einem Hook passt."""
        MEME_SIGNALS: dict[str, list[str]] = {
            "POV-Format":               ["pov:", "pov "],
            "Before/After-Subversion":  ["vorher", "nachher", "bevor"],
            "Reaktions-Split-Screen":   ["reagiert", "reaktion", "sieht"],
            "'Ich hätte nie gedacht'-Testimonial": ["nie gedacht", "nie erwartet"],
            "'Das Einzige was zählt'-Format": ["einzige was zählt", "was wirklich"],
        }
        for meme_name, signals in MEME_SIGNALS.items():
            if any(sig in hook_lower for sig in signals):
                return meme_name
        return None

    def _find_matching_transformation(
        self, hook_lower: str, transformation_input: str
    ) -> str | None:
        """Erkennt welches Transformationsmuster zu einer Idee passt."""
        combined = hook_lower + " " + transformation_input.lower()
        TRANSFORMATION_SIGNALS: dict[str, list[str]] = {
            "Kinderzeichnung-zu-3D":     ["zeichnung", "gezeichnet", "gemalt", "bild"],
            "Haustier-zu-Skulptur":      ["haustier", "hund", "katze", "tier", "hase"],
            "Baby-Abdruck-Skulptur":     ["baby", "säugling", "hand", "fuß", "abdruck"],
            "Lieblingsstofftier-Klon":   ["stoff", "kuschel", "teddybär", "plüsch"],
            "Handschrift-zu-3D-Relief":  ["handschrift", "geschrieben", "brief", "notiz"],
            "Gezeichnetes-Alter-Ego":    ["selbstporträt", "selbst gezeichnet", "sich selbst"],
        }
        for name, signals in TRANSFORMATION_SIGNALS.items():
            if any(sig in combined for sig in signals):
                return name
        return None

    @staticmethod
    def _randomized_pick(
        items: Sequence,
        n: int,
        pool_multiplier: int = 2,
    ) -> list:
        """
        Wählt n zufällige Einträge aus den top (n * pool_multiplier) Einträgen.
        Sorgt für Variation zwischen Runs ohne die Qualität stark zu senken.
        """
        pool = list(items[: n * pool_multiplier])
        if len(pool) <= n:
            return pool
        return random.sample(pool, n)
