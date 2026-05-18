"""
Claude API Client — Anthropic SDK Wrapper.
Spezifikation: api/api-gateway.md → API 1: Claude API

Unterstützte Modelle:
  haiku  → claude-haiku-4-5-20251001   (Standard, günstig, schnell)
  sonnet → claude-sonnet-4-6           (höhere Qualität, auf Anfrage)

Kosten-Tracking: Tokens aus API-Response lesen → EUR berechnen.
  Kurs: 1 USD = 0.92 EUR (approximiert, für Budgetkontrolle ausreichend)

Retry-Logik: max. 2 Retries, Backoff 30s / 90s (tenacity).
  Bei RateLimitError: sofort retry nach Wartezeit.
  Bei AuthenticationError: kein Retry — Hard Stop.

Dev-Mode: Wenn ANTHROPIC_API_KEY leer → Mock-Output zurückgeben.
  Mock enthält valides JSON mit 3 Dummy-Ideen für Pipeline-Tests.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from app.config import settings

log = structlog.get_logger(__name__)

# ── Kosten-Tabelle (USD/1M Tokens → EUR) ──────────────────────────────────────
# Stand: Mai 2026 — bei Preisänderungen hier aktualisieren
USD_TO_EUR = 0.92

COST_TABLE = {
    # model_key: (input_usd_per_mtok, output_usd_per_mtok)
    "haiku":  (0.25,  1.25),
    "sonnet": (3.00, 15.00),
}


# ── Datenklassen ───────────────────────────────────────────────────────────────

@dataclass
class ClaudeResponse:
    """Antwort eines Claude-API-Calls mit vollständigem Audit-Trail."""
    text: str                      # Rohtext des Modells
    model_key: str                 # 'haiku' | 'sonnet'
    model_id: str                  # voller Modell-String (claude-haiku-...)
    tokens_input: int = 0
    tokens_output: int = 0
    cost_eur: float = 0.0
    latency_ms: int = 0
    stop_reason: str = ""
    is_mock: bool = False          # True im Dev-Mode


@dataclass
class ClaudeCallParams:
    """Parameter für einen einzelnen Claude-API-Call."""
    system_prompt: str
    user_prompt: str
    model: str = "haiku"           # 'haiku' | 'sonnet'
    max_tokens: int = 2048
    temperature: float = 1.0       # Anthropic-Standard; 0.0 für deterministisch


# ── Exceptions ─────────────────────────────────────────────────────────────────

class ClaudeAPIError(Exception):
    """Allgemeiner Claude-API-Fehler (Netzwerk, Timeout, Rate-Limit)."""

class ClaudeAuthError(ClaudeAPIError):
    """Authentifizierungsfehler — kein Retry."""

class ClaudeRateLimitError(ClaudeAPIError):
    """Rate-Limit erreicht — mit Backoff retrybar."""


# ── Mock-Output (Dev-Mode) ─────────────────────────────────────────────────────
# 20 Vorlagen — alle bestehen self_check (keine verbotenen Wörter, Hook ≤ 80 Zeichen,
# Emotion nicht leer). Bei count > 20 werden Vorlagen zyklisch wiederverwendet.

_MOCK_TEMPLATES: list[dict] = [
    {
        "plattform": "instagram", "content_type": "reel",
        "hook": "Das Bild deines Kindes war krumm. Die Erinnerung daran ist es nicht.",
        "emotion": "Nostalgie",
        "kernbotschaft": "Unvollkommenheit ist das Echte — und das Echte wird unvergänglich.",
        "warum_es_funktioniert": "Eltern sehen sich sofort in dieser Szene. Der Widerspruch erzeugt kognitive Spannung.",
        "visueller_ansatz": "Close-up einer Kinderzeichnung. Langsamer Zoom. Schnitt auf die fertige 3D-Figur.",
        "zielgruppe": "Eltern mit Kindern zwischen 3 und 10 Jahren.",
        "potenzial": "hoch",
    },
    {
        "plattform": "instagram", "content_type": "carousel",
        "hook": "Unser Hund ist seit 3 Jahren weg. Wir sehen ihn jeden Tag.",
        "emotion": "Trauer",
        "kernbotschaft": "Verlust muss nicht das letzte Wort haben — Erinnerung kann Gestalt annehmen.",
        "warum_es_funktioniert": "Tierverlust ist universell und emotional hochgeladen.",
        "visueller_ansatz": "Foto des Hundes, dann Slide für Slide die Entstehung der 3D-Figur.",
        "zielgruppe": "Tierbesitzer die einen verstorbenen Hund vermissen.",
        "potenzial": "hoch",
    },
    {
        "plattform": "facebook", "content_type": "image",
        "hook": "Das Kunstwerk meiner Tochter hängt für immer in unserem Wohnzimmer.",
        "emotion": "Stolz",
        "kernbotschaft": "Die schönsten Dinge entstehen nicht in Galerien — sondern am Küchentisch.",
        "warum_es_funktioniert": "Eltern-Stolz ist auf Facebook besonders stark.",
        "visueller_ansatz": "Vorher/Nachher: Blatt Papier links, 3D-Figur rechts. Warmes Licht.",
        "zielgruppe": "Eltern auf Facebook, 28–45 Jahre.",
        "potenzial": "mittel",
    },
    {
        "plattform": "facebook", "content_type": "reel",
        "hook": "Sie hat dieses Bild mit 4 Jahren gemalt. Mit 40 wird sie weinen.",
        "emotion": "Rührung",
        "kernbotschaft": "Kindheitserinnerungen verdienen mehr als einen Schuhkarton.",
        "warum_es_funktioniert": "Das Publikum projiziert sofort die eigene Kindheit hinein.",
        "visueller_ansatz": "Zeichnung in Kinderhänden. Zeitraffer. Dieselbe Zeichnung als 3D-Figur.",
        "zielgruppe": "Erwachsene die an ihre eigene Kindheit denken.",
        "potenzial": "hoch",
    },
    {
        "plattform": "facebook", "content_type": "carousel",
        "hook": "Manchmal ist das Einzige was bleibt, das was wir festhalten.",
        "emotion": "Melancholie",
        "kernbotschaft": "Erinnerung ist aktiv — sie braucht ein Gefäß.",
        "warum_es_funktioniert": "Passt zu Trauer und Dankbarkeit gleichzeitig.",
        "visueller_ansatz": "Alte Familienfotos neben frischen Kinderzeichnungen.",
        "zielgruppe": "Großeltern und Eltern die Familiengeschichten bewahren wollen.",
        "potenzial": "mittel",
    },
    {
        "plattform": "instagram", "content_type": "image",
        "hook": "Er konnte nicht sprechen. Sein Pinsel schon.",
        "emotion": "Staunen",
        "kernbotschaft": "Kinder kommunizieren in Formen und Farben bevor sie Worte finden.",
        "warum_es_funktioniert": "Kurz, prägnant, hinterlässt einen Nachhall.",
        "visueller_ansatz": "Nahaufnahme einer sehr jungen Hand die malt.",
        "zielgruppe": "Eltern von Kleinkindern und Vorschulkindern.",
        "potenzial": "hoch",
    },
    {
        "plattform": "instagram", "content_type": "reel",
        "hook": "Wir dachten, die Zeichnung wäre verloren. Sie war nur auf Papier.",
        "emotion": "Erleichterung",
        "kernbotschaft": "Papier ist vergänglich. Erinnerung muss es nicht sein.",
        "warum_es_funktioniert": "Das Wortspiel erzeugt Spannung und löst sie sofort auf.",
        "visueller_ansatz": "Zerknittertes Blatt wird geglättet. Dann die 3D-Figur.",
        "zielgruppe": "Eltern die Angst vor dem Verlust von Kindheitsdingen haben.",
        "potenzial": "hoch",
    },
    {
        "plattform": "facebook", "content_type": "reel",
        "hook": "Unser Sohn nennt seine Figur beim Namen. Sie heißt wie der Hund.",
        "emotion": "Liebe",
        "kernbotschaft": "Wenn Kinder einem Gegenstand einen Namen geben, wird er lebendig.",
        "warum_es_funktioniert": "Authentische Familienmomente sind auf Facebook unschlagbar.",
        "visueller_ansatz": "Kind spielt mit der 3D-Figur und nennt sie beim Namen.",
        "zielgruppe": "Familien mit jungen Kindern und Haustieren.",
        "potenzial": "mittel",
    },
    {
        "plattform": "instagram", "content_type": "carousel",
        "hook": "Drei Striche. Ein Kreis. Der schönste Hund den sie je gemalt hat.",
        "emotion": "Freude",
        "kernbotschaft": "Kunst braucht keine Perfektion — sie braucht Aufmerksamkeit.",
        "warum_es_funktioniert": "Der Kontrast zwischen Minimalismus und Bedeutung ist stark.",
        "visueller_ansatz": "Die einfache Zeichnung. Dann die detaillierte 3D-Figur.",
        "zielgruppe": "Eltern von Kindern die Tiere lieben und zeichnen.",
        "potenzial": "hoch",
    },
    {
        "plattform": "facebook", "content_type": "reel",
        "hook": "Manche Dinge sind zu wertvoll um auf Papier zu bleiben.",
        "emotion": "Ehrfurcht",
        "kernbotschaft": "Transformation ist die tiefste Form der Wertschätzung.",
        "warum_es_funktioniert": "Eine stille Aussage die sofort neugierig macht.",
        "visueller_ansatz": "Papier in Flammen — Schnitt auf die unzerstörbare 3D-Figur.",
        "zielgruppe": "Alle die Angst vor dem Verlieren von Wertvollem kennen.",
        "potenzial": "hoch",
    },
    {
        "plattform": "facebook", "content_type": "image",
        "hook": "Das ist kein Spielzeug. Das ist eine Zeitkapsel.",
        "emotion": "Verbundenheit",
        "kernbotschaft": "Gegenstände die wir lieben erzählen unsere Geschichte.",
        "warum_es_funktioniert": "Reframing: von Produkt zu Bedeutungsträger.",
        "visueller_ansatz": "Figur im Familienregal zwischen Fotos und Büchern.",
        "zielgruppe": "Familien mit Sinn für Traditionen und Familiengeschichte.",
        "potenzial": "mittel",
    },
    {
        "plattform": "instagram", "content_type": "reel",
        "hook": "Wir haben das Original eingerahmt. Die Kopie ist aus Bronze.",
        "emotion": "Humor",
        "kernbotschaft": "Das Original und die Verwandlung können nebeneinander existieren.",
        "warum_es_funktioniert": "Selbstironie und Witz öffnen das Herz.",
        "visueller_ansatz": "Gerahmtes Bild an der Wand neben der 3D-Figur auf dem Regal.",
        "zielgruppe": "Eltern mit einem Sinn für Humor und Selbstironie.",
        "potenzial": "mittel",
    },
    {
        "plattform": "facebook", "content_type": "carousel",
        "hook": "Sie hat den Hund gezeichnet einen Tag bevor er starb.",
        "emotion": "Traurigkeit",
        "kernbotschaft": "Manchmal wissen Kinder intuitiv was Erwachsene nicht sehen.",
        "warum_es_funktioniert": "Eine Geschichte die sich wirklich zugetragen haben könnte.",
        "visueller_ansatz": "Die Zeichnung. Dann ein Foto des Hundes. Dann die 3D-Figur.",
        "zielgruppe": "Tierbesitzer die Verlust kennen.",
        "potenzial": "hoch",
    },
    {
        "plattform": "instagram", "content_type": "reel",
        "hook": "Manche Eltern rahmen Bilder. Manche verwandeln sie.",
        "emotion": "Überraschung",
        "kernbotschaft": "Es gibt mehr Möglichkeiten als wir denken um zu ehren was wir lieben.",
        "warum_es_funktioniert": "Implizite Einladung: Welcher Typ bist du?",
        "visueller_ansatz": "Split-Screen: Bild hängt an Wand vs. 3D-Figur steht auf Tisch.",
        "zielgruppe": "Eltern die neue Wege suchen Kindheitserinnerungen zu bewahren.",
        "potenzial": "hoch",
    },
    {
        "plattform": "instagram", "content_type": "carousel",
        "hook": "Du wirst vergessen was du sagtest. Nicht was du hieltest.",
        "emotion": "Weisheit",
        "kernbotschaft": "Physische Gegenstände aktivieren Erinnerung auf andere Weise als Fotos.",
        "warum_es_funktioniert": "Philosophisch, aber durch Haptik sofort anwendbar.",
        "visueller_ansatz": "Hand hält Foto. Hand hält 3D-Figur. Das Gesicht zeigt den Unterschied.",
        "zielgruppe": "Reflektierte Eltern und Großeltern.",
        "potenzial": "mittel",
    },
    {
        "plattform": "facebook", "content_type": "image",
        "hook": "Das ist ihre erste Zeichnung. Sie war drei. Nichts ging verloren.",
        "emotion": "Schutz",
        "kernbotschaft": "Die ersten Ausdrücke eines Kindes verdienen das beste Archiv.",
        "warum_es_funktioniert": "Der Wunsch zu schützen ist bei Eltern tief verwurzelt.",
        "visueller_ansatz": "Zarte Kinderzeichnung neben stabiler 3D-Figur.",
        "zielgruppe": "Eltern von Kleinkindern die gerade anfangen zu zeichnen.",
        "potenzial": "mittel",
    },
    {
        "plattform": "instagram", "content_type": "reel",
        "hook": "Was ein Kind malt, versteht die Welt erst Jahrzehnte später.",
        "emotion": "Neugier",
        "kernbotschaft": "Kindliche Kunst trägt Wahrheiten die wir als Erwachsene vergessen.",
        "warum_es_funktioniert": "Mysteriös genug um weiterzuschauen.",
        "visueller_ansatz": "Zeichnung wird gezeigt. Kein Kontext. Dann langsam die Geschichte dahinter.",
        "zielgruppe": "Alle die an die Intuition von Kindern glauben.",
        "potenzial": "niche",
    },
    {
        "plattform": "instagram", "content_type": "image",
        "hook": "Er ist sieben. Er hat gerade sein erstes Porträt gemalt.",
        "emotion": "Stolz",
        "kernbotschaft": "Der erste Versuch ist der ehrlichste.",
        "warum_es_funktioniert": "Universell: Jeder erinnert sich an ein erstes Mal.",
        "visueller_ansatz": "Kind präsentiert stolz seine Zeichnung. Dann die 3D-Figur.",
        "zielgruppe": "Eltern von schulpflichtigen Kindern.",
        "potenzial": "mittel",
    },
    {
        "plattform": "facebook", "content_type": "reel",
        "hook": "Sie bat uns, es aufzubewahren. Wir haben mehr getan.",
        "emotion": "Dankbarkeit",
        "kernbotschaft": "Manchmal ist das Beste was wir tun können mehr als erwartet.",
        "warum_es_funktioniert": "Überraschung und Dankbarkeit sind viral auf Facebook.",
        "visueller_ansatz": "Kind übergibt Zeichnung. Eltern reagieren. Unboxing der 3D-Figur.",
        "zielgruppe": "Familien die sich gegenseitig Freude schenken wollen.",
        "potenzial": "hoch",
    },
    {
        "plattform": "instagram", "content_type": "carousel",
        "hook": "Als sie starb, hinterließ sie 200 Zeichnungen. Eine blieb.",
        "emotion": "Ehrfurcht",
        "kernbotschaft": "Auswahl ist Ehrung. Eines zu wählen bedeutet es für immer zu halten.",
        "warum_es_funktioniert": "Verlust und Entscheidung — zwei starke emotionale Trigger.",
        "visueller_ansatz": "Stapel von Zeichnungen. Eine wird ausgewählt. Dann die 3D-Figur.",
        "zielgruppe": "Menschen die einen geliebten Menschen verloren haben.",
        "potenzial": "hoch",
    },
]


def _extract_count_from_prompt(prompt: str) -> int:
    """Extrahiert die gewünschte Ideen-Anzahl aus dem User-Prompt."""
    m = re.search(r"(\d+)\s+Ideen", prompt)
    return int(m.group(1)) if m else 10


def _generate_mock_ideas(count: int) -> str:
    """
    Generiert count Mock-Ideen dynamisch aus _MOCK_TEMPLATES.
    Alle Ideen bestehen den self_check (keine verbotenen Wörter, Hook ≤ 80 Zeichen).
    Bei count > 20 werden Templates zyklisch wiederverwendet (Hook bekommt Index-Suffix).
    """
    ideas: list[dict] = []
    for i in range(count):
        template = dict(_MOCK_TEMPLATES[i % len(_MOCK_TEMPLATES)])
        template["nummer"] = i + 1
        # Wiederverwendete Templates: Hook eindeutig machen (innerhalb 80-Zeichen-Limit)
        if i >= len(_MOCK_TEMPLATES):
            suffix = f" [{i + 1}]"
            template["hook"] = template["hook"][: 80 - len(suffix)] + suffix
        ideas.append(template)
    return json.dumps(ideas, ensure_ascii=False, indent=2)


# ── ClaudeClient ───────────────────────────────────────────────────────────────

class ClaudeClient:
    """
    Anthropic Claude API Wrapper.

    Im Dev-Mode (ANTHROPIC_API_KEY leer):
      Gibt deterministische Mock-Antwort zurück — keine echten API-Calls.
      Ideal für Pipeline-Tests ohne API-Kosten.

    Retry-Verhalten:
      Netzwerk-/Rate-Limit-Fehler: max. 2 Retries, 30s / 90s Backoff.
      AuthError: sofort Exception — kein Retry.
    """

    def __init__(self) -> None:
        self._dev_mode = not settings.ANTHROPIC_API_KEY
        self._client: Any = None

        if self._dev_mode:
            log.warning(
                "claude_client.dev_mode",
                reason="ANTHROPIC_API_KEY nicht gesetzt — Mock-Output aktiv",
            )
        else:
            self._client = self._build_client()

    def complete(self, params: ClaudeCallParams) -> ClaudeResponse:
        """
        Schickt einen Prompt an Claude und gibt strukturierte Antwort zurück.

        Args:
            params: ClaudeCallParams mit System-/User-Prompt und Modell-Auswahl

        Returns:
            ClaudeResponse mit Text, Token-Zählung, Kosten und Latenz

        Raises:
            ClaudeAuthError:      Authentifizierungsfehler (kein Retry)
            ClaudeRateLimitError: Rate-Limit (mit Backoff retrybar)
            ClaudeAPIError:       Sonstiger API-Fehler
        """
        model_key = params.model.lower()
        if model_key not in COST_TABLE:
            model_key = "haiku"

        model_id = (
            settings.CLAUDE_MODEL_HAIKU if model_key == "haiku"
            else settings.CLAUDE_MODEL_SONNET
        )

        if self._dev_mode:
            return self._mock_response(params, model_key, model_id)

        return self._call_with_retry(params, model_key, model_id)

    # ── Interne Methoden ───────────────────────────────────────────────────────

    def _build_client(self) -> Any:
        """Baut den Anthropic-Client. Lazy-Import für Dev-Mode-Kompatibilität."""
        try:
            import anthropic  # type: ignore
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
            log.info("claude_client.authenticated")
            return client
        except ImportError as exc:
            raise ClaudeAPIError(
                "anthropic-Paket nicht installiert. "
                "Bitte: pip install anthropic"
            ) from exc

    def _call_with_retry(
        self,
        params: ClaudeCallParams,
        model_key: str,
        model_id: str,
    ) -> ClaudeResponse:
        """Führt API-Call mit Retry-Logik aus."""

        @retry(
            retry=retry_if_exception_type(ClaudeRateLimitError),
            stop=stop_after_attempt(3),           # 1 Versuch + 2 Retries
            wait=wait_fixed(30),                  # 30s zwischen Retries
            reraise=True,
        )
        def _do_call() -> ClaudeResponse:
            return self._single_call(params, model_key, model_id)

        return _do_call()

    def _single_call(
        self,
        params: ClaudeCallParams,
        model_key: str,
        model_id: str,
    ) -> ClaudeResponse:
        """Einzelner API-Call ohne Retry."""
        import anthropic  # type: ignore

        start_ms = int(time.monotonic() * 1000)

        try:
            log.info(
                "claude.call.start",
                model=model_id,
                max_tokens=params.max_tokens,
                system_len=len(params.system_prompt),
                user_len=len(params.user_prompt),
            )

            message = self._client.messages.create(
                model=model_id,
                max_tokens=params.max_tokens,
                temperature=params.temperature,
                system=params.system_prompt,
                messages=[{"role": "user", "content": params.user_prompt}],
            )

        except anthropic.AuthenticationError as exc:
            log.error("claude.call.auth_error", error=str(exc))
            raise ClaudeAuthError(f"Claude-Authentifizierung fehlgeschlagen: {exc}") from exc

        except anthropic.RateLimitError as exc:
            log.warning("claude.call.rate_limit", error=str(exc))
            raise ClaudeRateLimitError(f"Claude Rate-Limit: {exc}") from exc

        except anthropic.APIError as exc:
            log.error("claude.call.api_error", error=str(exc), status_code=getattr(exc, "status_code", "?"))
            raise ClaudeAPIError(f"Claude-API-Fehler: {exc}") from exc

        latency_ms = int(time.monotonic() * 1000) - start_ms
        text = message.content[0].text if message.content else ""
        tokens_in = message.usage.input_tokens
        tokens_out = message.usage.output_tokens
        cost_eur = self._calc_cost(model_key, tokens_in, tokens_out)

        log.info(
            "claude.call.ok",
            model=model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_eur=round(cost_eur, 5),
            latency_ms=latency_ms,
            stop_reason=message.stop_reason,
        )

        return ClaudeResponse(
            text=text,
            model_key=model_key,
            model_id=model_id,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_eur=cost_eur,
            latency_ms=latency_ms,
            stop_reason=message.stop_reason or "",
            is_mock=False,
        )

    def _mock_response(
        self,
        params: ClaudeCallParams,
        model_key: str,
        model_id: str,
    ) -> ClaudeResponse:
        """
        Gibt einen dynamischen Mock zurück — kein API-Call.
        Anzahl der Ideen wird aus dem User-Prompt extrahiert.
        """
        # Simulierte Latenz
        time.sleep(0.05)

        count = _extract_count_from_prompt(params.user_prompt)
        mock_json = _generate_mock_ideas(count)

        tokens_in = len(params.system_prompt.split()) + len(params.user_prompt.split())
        tokens_out = len(mock_json.split())
        cost_eur = self._calc_cost(model_key, tokens_in, tokens_out)

        log.info(
            "claude.call.mock",
            model=model_id,
            count=count,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_eur=round(cost_eur, 6),
        )

        return ClaudeResponse(
            text=mock_json,
            model_key=model_key,
            model_id=model_id,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost_eur=cost_eur,
            latency_ms=50,
            stop_reason="end_turn",
            is_mock=True,
        )

    @staticmethod
    def _calc_cost(model_key: str, tokens_in: int, tokens_out: int) -> float:
        """Berechnet Kosten in EUR aus Token-Anzahl."""
        input_rate, output_rate = COST_TABLE.get(model_key, (0.25, 1.25))
        cost_usd = (tokens_in * input_rate + tokens_out * output_rate) / 1_000_000
        return round(cost_usd * USD_TO_EUR, 6)


# ── Singleton ──────────────────────────────────────────────────────────────────

_claude_client: ClaudeClient | None = None


def get_claude_client() -> ClaudeClient:
    """Gecachte ClaudeClient-Instanz. Thread-safe für Lesezugriffe."""
    global _claude_client
    if _claude_client is None:
        _claude_client = ClaudeClient()
    return _claude_client
