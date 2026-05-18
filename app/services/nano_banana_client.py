"""
Nano Banana Client — Google Gemini Image Generation API Wrapper.

"Nano Banana" = interner Projektname für Gemini 2.0 Flash Image Generation.

Modell: gemini-2.0-flash-exp-image-generation (konfigurierbar via GEMINI_IMAGE_MODEL)
Authentifizierung: GEMINI_API_KEY (Google AI Studio Key)

Kosten-Tracking:
  EUR pro Bild aus GEMINI_IMAGE_COST_EUR_PER_IMAGE (Settings).
  Kumuliert pro Job — Abbruch wenn IMAGE_COST_CAP_EUR_PER_JOB überschritten.

Retry-Logik:
  Max. 3 API-Retries (tenacity), exponentieller Backoff 5s/15s/45s.
  Bei QuotaError: 60s warten, dann retry.
  Bei AuthError: sofort Exception — kein Retry.

Dev-Mode:
  Wenn GEMINI_API_KEY leer → kein API-Call.
  Gibt echtes minimales 1×1 PNG zurück (testbares Bytes-Objekt).
  Datei: kein externes Asset — PNG wird inline erzeugt.
"""

from __future__ import annotations

import base64
import io
import struct
import time
import zlib
from dataclasses import dataclass
from typing import Any

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.models.image_generation import BuiltPrompt, GeneratedImage

log = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class NanoBananaError(Exception):
    """Allgemeiner Gemini-Bildgenerierungsfehler."""

class NanoBananaAuthError(NanoBananaError):
    """API-Key ungültig — kein Retry."""

class NanoBananaQuotaError(NanoBananaError):
    """Quota erschöpft — Retry nach Wartezeit."""

class NanoBananaContentError(NanoBananaError):
    """Prompt durch Safety-Filter blockiert — Prompt ändern, kein direkter Retry."""

class NanoBananaCostCapError(NanoBananaError):
    """Job-Kosten-Cap überschritten — Job abbrechen."""


# ─────────────────────────────────────────────────────────────────────────────
# Mock-PNG Generator (Dev-Mode)
# ─────────────────────────────────────────────────────────────────────────────

def _make_mock_png(width: int = 32, height: int = 32) -> bytes:
    """
    Erzeugt ein minimales gültiges PNG mit warmem Beige (#F5E6D3 — 3D-Memories-Farbe).
    Kein externes Asset — alles inline. Gibt echtes PNG-Bytes-Objekt zurück.
    """
    def _chunk(name: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + name + data
        return c + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)

    # PNG-Header
    header = b"\x89PNG\r\n\x1a\n"

    # IHDR: width, height, bit_depth=8, color_type=2 (RGB), compress=0, filter=0, interlace=0
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = _chunk(b"IHDR", ihdr_data)

    # IDAT: einfache Zeilen mit Warm-Beige RGB (245, 230, 211)
    raw_rows = b""
    for _ in range(height):
        raw_rows += b"\x00" + bytes([245, 230, 211]) * width

    compressed = zlib.compress(raw_rows)
    idat = _chunk(b"IDAT", compressed)

    # IEND
    iend = _chunk(b"IEND", b"")

    return header + ihdr + idat + iend


# ─────────────────────────────────────────────────────────────────────────────
# NanoBananaClient
# ─────────────────────────────────────────────────────────────────────────────

class NanoBananaClient:
    """
    Google Gemini Image Generation API Wrapper.

    Im Dev-Mode (GEMINI_API_KEY leer):
      Gibt ein echtes minimales PNG zurück.
      Keine echten API-Calls, keine Kosten.
      Simulierte Latenz: 200ms.

    Singleton-Nutzung empfohlen — get_nano_banana_client() verwenden.
    """

    def __init__(self) -> None:
        self._dev_mode = not settings.GEMINI_API_KEY
        self._client: Any = None
        self._accumulated_cost_eur: float = 0.0

        if self._dev_mode:
            log.warning(
                "nano_banana.dev_mode",
                reason="GEMINI_API_KEY nicht gesetzt — Mock-PNG aktiv",
            )
        else:
            self._client = self._build_client()

    # ─────────────────────────────────────────────────────────────────────────
    # Öffentliche API
    # ─────────────────────────────────────────────────────────────────────────

    def generate(self, prompt: BuiltPrompt) -> GeneratedImage:
        """
        Generiert ein einzelnes Bild aus einem BuiltPrompt.

        Args:
            prompt: BuiltPrompt von ImagePromptBuilder

        Returns:
            GeneratedImage mit Bytes, Dimensionen und Kosten

        Raises:
            NanoBananaAuthError:    API-Key ungültig
            NanoBananaQuotaError:   Quota erschöpft
            NanoBananaContentError: Safety-Filter blockiert
            NanoBananaCostCapError: Job-Kosten-Cap überschritten
            NanoBananaError:        Sonstiger Fehler
        """
        # Kosten-Cap-Check vor dem Call
        cost_estimate = settings.GEMINI_IMAGE_COST_EUR_PER_IMAGE
        if self._accumulated_cost_eur + cost_estimate > settings.IMAGE_COST_CAP_EUR_PER_JOB:
            raise NanoBananaCostCapError(
                f"Job-Kosten-Cap erreicht: {self._accumulated_cost_eur:.4f} EUR "
                f"+ {cost_estimate:.4f} EUR > {settings.IMAGE_COST_CAP_EUR_PER_JOB} EUR"
            )

        if self._dev_mode:
            return self._generate_mock(prompt)

        return self._generate_with_retry(prompt)

    def reset_job_cost(self) -> None:
        """Setzt akkumulierte Job-Kosten zurück (aufrufen beim Start eines neuen Jobs)."""
        self._accumulated_cost_eur = 0.0

    @property
    def accumulated_cost_eur(self) -> float:
        return self._accumulated_cost_eur

    # ─────────────────────────────────────────────────────────────────────────
    # Generierung mit Retry
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_with_retry(self, prompt: BuiltPrompt) -> GeneratedImage:
        """Führt API-Call mit exponentiellem Backoff durch."""

        @retry(
            retry=retry_if_exception_type(NanoBananaQuotaError),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=5, min=5, max=60),
            reraise=True,
        )
        def _do_call() -> GeneratedImage:
            return self._single_generate(prompt)

        return _do_call()

    def _single_generate(self, prompt: BuiltPrompt) -> GeneratedImage:
        """Einzelner API-Call ohne Retry. Nutzt google.genai SDK (v2+)."""
        from google import genai as google_genai          # type: ignore
        from google.genai import types as genai_types     # type: ignore

        start_ms = int(time.monotonic() * 1000)

        log.info(
            "nano_banana.generate.start",
            model=settings.GEMINI_IMAGE_MODEL,
            prompt_len=len(prompt.positive_prompt),
            negative_len=len(prompt.negative_prompt),
            aspect_ratio=prompt.aspect_ratio,
            platform=prompt.platform,
            content_type=prompt.content_type,
        )

        try:
            # Prompt zusammenbauen — Negativprompt als Avoid-Anweisung
            full_prompt = prompt.positive_prompt
            if prompt.negative_prompt:
                full_prompt += f"\n\nAvoid: {prompt.negative_prompt}"

            response = self._client.models.generate_content(
                model=settings.GEMINI_IMAGE_MODEL,
                contents=full_prompt,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                ),
            )

        except Exception as exc:
            return self._handle_gemini_error(exc)

        # Response parsen
        image_bytes = self._extract_image_bytes(response)
        generation_time_ms = int(time.monotonic() * 1000) - start_ms
        cost_eur = settings.GEMINI_IMAGE_COST_EUR_PER_IMAGE
        self._accumulated_cost_eur += cost_eur

        log.info(
            "nano_banana.generate.ok",
            size_bytes=len(image_bytes),
            width=prompt.width,
            height=prompt.height,
            generation_time_ms=generation_time_ms,
            cost_eur=cost_eur,
            accumulated_eur=self._accumulated_cost_eur,
        )

        return GeneratedImage(
            image_bytes=image_bytes,
            width=prompt.width,
            height=prompt.height,
            format="jpeg",
            mime_type="image/jpeg",
            model=settings.GEMINI_IMAGE_MODEL,
            generation_time_ms=generation_time_ms,
            cost_eur=cost_eur,
            is_mock=False,
            api_response_id="",
        )

    def _extract_image_bytes(self, response: Any) -> bytes:
        """Extrahiert Bild-Bytes aus google.genai-Response."""
        try:
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    data = part.inline_data.data
                    # data kann bytes oder base64-String sein
                    if isinstance(data, str):
                        return base64.b64decode(data)
                    return bytes(data)
        except (IndexError, AttributeError) as exc:
            raise NanoBananaError(
                f"Kein Bild in Gemini-Response gefunden: {exc}"
            ) from exc

        raise NanoBananaError("Response enthält kein Bild-Part.")

    def _handle_gemini_error(self, exc: Exception) -> GeneratedImage:
        """Klassifiziert Gemini-Fehler und wirft passende Exception."""
        error_str = str(exc).lower()

        if "api_key" in error_str or "authentication" in error_str or "permission" in error_str:
            log.error("nano_banana.auth_error", error=str(exc))
            raise NanoBananaAuthError(f"Gemini Auth-Fehler: {exc}") from exc

        if "quota" in error_str or "resource_exhausted" in error_str or "429" in error_str:
            log.warning("nano_banana.quota_error", error=str(exc))
            raise NanoBananaQuotaError(f"Gemini Quota erschöpft: {exc}") from exc

        if "safety" in error_str or "blocked" in error_str or "harmful" in error_str:
            log.warning("nano_banana.content_blocked", error=str(exc))
            raise NanoBananaContentError(
                f"Gemini Safety-Filter: Prompt blockiert. {exc}"
            ) from exc

        log.error("nano_banana.api_error", error=str(exc))
        raise NanoBananaError(f"Gemini-API-Fehler: {exc}") from exc

    # ─────────────────────────────────────────────────────────────────────────
    # Dev-Mode Mock
    # ─────────────────────────────────────────────────────────────────────────

    def _generate_mock(self, prompt: BuiltPrompt) -> GeneratedImage:
        """Gibt ein echtes minimales PNG zurück — kein API-Call."""
        time.sleep(0.2)  # Simulierte Latenz

        mock_png = _make_mock_png(
            width=min(prompt.width, 64),
            height=min(prompt.height, 64),
        )
        cost_eur = settings.GEMINI_IMAGE_COST_EUR_PER_IMAGE
        self._accumulated_cost_eur += cost_eur

        log.info(
            "nano_banana.mock",
            size_bytes=len(mock_png),
            platform=prompt.platform,
            content_type=prompt.content_type,
            cost_eur=cost_eur,
        )

        return GeneratedImage(
            image_bytes=mock_png,
            width=prompt.width,
            height=prompt.height,
            format="png",
            mime_type="image/png",
            model=settings.GEMINI_IMAGE_MODEL + "-MOCK",
            generation_time_ms=200,
            cost_eur=cost_eur,
            is_mock=True,
            api_response_id="MOCK",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Initialisierung
    # ─────────────────────────────────────────────────────────────────────────

    def _build_client(self) -> Any:
        """Initialisiert google.genai Client (SDK v2+)."""
        try:
            from google import genai as google_genai  # type: ignore
            client = google_genai.Client(api_key=settings.GEMINI_API_KEY)
            log.info(
                "nano_banana.authenticated",
                model=settings.GEMINI_IMAGE_MODEL,
            )
            return client
        except ImportError as exc:
            raise NanoBananaError(
                "google-genai nicht installiert. "
                "Bitte: pip install google-genai"
            ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_nano_banana_client: NanoBananaClient | None = None


def get_nano_banana_client() -> NanoBananaClient:
    """Gecachte NanoBananaClient-Instanz."""
    global _nano_banana_client
    if _nano_banana_client is None:
        _nano_banana_client = NanoBananaClient()
    return _nano_banana_client
