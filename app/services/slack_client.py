"""
Slack Web API Client.
Spezifikation: api/api-gateway.md → API 2: Slack API

Methoden:
  post_message(channel, text, blocks)     → message_ts (str)
  post_ephemeral(channel, user, text)     → None
  update_message(channel, ts, blocks)     → None
  post_alert(text, level)                 → None
    Level 1: #3dm-errors
    Level 2: #3dm-errors + DM an Markus
    Level 3: #3dm-errors + #3dm-control + DM an Markus

Rate-Limit: 1 chat.postMessage/Sekunde (Tier 1, Standard-Botschaften).
  Beim Posten von Ideen-Batches: 500ms Pause zwischen Karten.

Sicherheit: SLACK_BOT_TOKEN niemals in Logs ausgeben.

Dev-Mode: Wenn SLACK_BOT_TOKEN leer → kein API-Call, nur loggen.
  Gibt Dummy-Timestamps zurück ("MOCK_TS_<uuid>").
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

from app.config import settings

log = structlog.get_logger(__name__)


# ── Exceptions ─────────────────────────────────────────────────────────────────

class SlackAPIError(Exception):
    """Allgemeiner Slack-API-Fehler."""

class SlackRateLimitError(SlackAPIError):
    """Rate-Limit erreicht — kurze Wartezeit, dann Retry."""

class SlackAuthError(SlackAPIError):
    """invalid_auth oder token_revoked — kein Retry, Alert nötig."""


# ── SlackClient ────────────────────────────────────────────────────────────────

class SlackClient:
    """
    Slack Web API Wrapper via slack-sdk.

    Im Dev-Mode (SLACK_BOT_TOKEN leer):
      Alle Methoden loggen was sie senden würden.
      Gibt Dummy-Timestamps zurück.
      Kein echter API-Call, kein SDK-Import nötig.
    """

    def __init__(self) -> None:
        self._dev_mode = not settings.SLACK_BOT_TOKEN
        self._client: Any = None

        if self._dev_mode:
            log.warning(
                "slack_client.dev_mode",
                reason="SLACK_BOT_TOKEN nicht gesetzt — alle Posts simuliert",
            )
        else:
            self._client = self._build_client()

    # ── Öffentliche API ────────────────────────────────────────────────────────

    def post_message(
        self,
        channel: str,
        text: str = "",
        blocks: list[dict] | None = None,
        thread_ts: str | None = None,
    ) -> str:
        """
        Sendet eine Nachricht in einen Channel.

        Args:
            channel:   Channel-ID oder Name (#3dm-control)
            text:      Fallback-Text (für Benachrichtigungen ohne Block-Rendering)
            blocks:    Block-Kit-Liste (optional, überschreibt text im UI)
            thread_ts: Thread-Timestamp — Antwort in Thread wenn gesetzt

        Returns:
            message_ts — Timestamp der gesendeten Nachricht (für Updates/Threads)

        Raises:
            SlackAuthError:      Token ungültig
            SlackRateLimitError: Rate-Limit
            SlackAPIError:       Sonstiger Fehler
        """
        if self._dev_mode:
            ts = f"MOCK_TS_{uuid.uuid4().hex[:8]}"
            log.info(
                "slack.post_message.simulated",
                channel=channel,
                text_preview=(text or "")[:80],
                blocks_count=len(blocks) if blocks else 0,
                mock_ts=ts,
            )
            return ts

        try:
            kwargs: dict = {
                "channel": channel,
                "text": text or " ",   # Slack erlaubt keinen leeren text
            }
            if blocks:
                kwargs["blocks"] = blocks
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            response = self._client.chat_postMessage(**kwargs)
            ts = response["ts"]
            log.info(
                "slack.post_message.ok",
                channel=channel,
                ts=ts,
                blocks_count=len(blocks) if blocks else 0,
            )
            return ts

        except Exception as exc:
            return self._handle_slack_error("post_message", exc)

    def post_ephemeral(
        self,
        channel: str,
        user: str,
        text: str = "",
        blocks: list[dict] | None = None,
    ) -> None:
        """
        Sendet eine Ephemeral-Nachricht (nur für einen User sichtbar).

        Args:
            channel: Channel-ID oder Name
            user:    Slack User-ID (Empfänger)
            text:    Nachrichtentext (Fallback)
            blocks:  Block-Kit-Liste (optional)
        """
        if self._dev_mode:
            log.info(
                "slack.post_ephemeral.simulated",
                channel=channel,
                user=user,
                text_preview=(text or "")[:80],
                blocks_count=len(blocks) if blocks else 0,
            )
            return

        try:
            kwargs: dict = {
                "channel": channel,
                "user": user,
                "text": text or " ",
            }
            if blocks:
                kwargs["blocks"] = blocks

            self._client.chat_postEphemeral(**kwargs)
            log.info("slack.post_ephemeral.ok", channel=channel, user=user)

        except Exception as exc:
            self._handle_slack_error("post_ephemeral", exc)

    def update_message(
        self,
        channel: str,
        ts: str,
        text: str = "",
        blocks: list[dict] | None = None,
    ) -> None:
        """
        Aktualisiert eine bestehende Nachricht (z.B. Buttons durch Status ersetzen).

        Args:
            channel: Channel-ID
            ts:      Timestamp der zu aktualisierenden Nachricht
            text:    Neuer Fallback-Text
            blocks:  Neue Block-Kit-Liste
        """
        if self._dev_mode:
            log.info(
                "slack.update_message.simulated",
                channel=channel,
                ts=ts,
                text_preview=(text or "")[:80],
                blocks_count=len(blocks) if blocks else 0,
            )
            return

        try:
            kwargs: dict = {
                "channel": channel,
                "ts": ts,
                "text": text or " ",
            }
            if blocks:
                kwargs["blocks"] = blocks

            self._client.chat_update(**kwargs)
            log.info("slack.update_message.ok", channel=channel, ts=ts)

        except Exception as exc:
            self._handle_slack_error("update_message", exc)

    def post_alert(self, text: str, level: int = 1) -> None:
        """
        Sendet einen System-Alert an die konfigurierten Channels.

        Level 1: #3dm-errors
        Level 2: #3dm-errors + DM an Markus (SLACK_DM_USER_ID)
        Level 3: #3dm-errors + #3dm-control + DM an Markus

        Args:
            text:  Alert-Text
            level: Alert-Level 1-3 (default: 1)
        """
        targets: list[str] = [settings.SLACK_CHANNEL_ERRORS]

        if level >= 2 and settings.SLACK_DM_USER_ID:
            targets.append(settings.SLACK_DM_USER_ID)

        if level >= 3:
            targets.append(settings.SLACK_CHANNEL_CONTROL)

        log.warning(
            "slack.alert",
            level=level,
            targets=targets,
            text_preview=text[:120],
        )

        for target in targets:
            try:
                self.post_message(
                    channel=target,
                    text=f":rotating_light: *ALERT L{level}:* {text}",
                )
                if not self._dev_mode:
                    time.sleep(0.5)
            except Exception as exc:
                log.error("slack.alert.send_failed", target=target, error=str(exc))

    def pause_between_messages(self, ms: int = 500) -> None:
        """Pausiert zwischen Nachrichten (Rate-Limit schonen). Nur im echten Modus."""
        if not self._dev_mode:
            time.sleep(ms / 1000)

    # ── Interne Hilfsmethoden ──────────────────────────────────────────────────

    def _build_client(self) -> Any:
        """Baut den Slack WebClient. Lazy-Import für Dev-Mode."""
        try:
            from slack_sdk import WebClient  # type: ignore
            client = WebClient(token=settings.SLACK_BOT_TOKEN)
            log.info("slack_client.authenticated")
            return client
        except ImportError as exc:
            raise SlackAPIError(
                "slack-sdk nicht installiert. Bitte: pip install slack-sdk"
            ) from exc

    def _handle_slack_error(self, method: str, exc: Exception) -> str:
        """Klassifiziert Slack-Fehler und wirft passende Exception."""
        try:
            from slack_sdk.errors import SlackApiError  # type: ignore

            if isinstance(exc, SlackApiError):
                error_code = exc.response.get("error", "unknown")

                if error_code in ("invalid_auth", "token_revoked", "account_inactive"):
                    log.error("slack.auth_error", method=method, error_code=error_code)
                    raise SlackAuthError(f"Slack Auth-Fehler: {error_code}") from exc

                if error_code == "ratelimited":
                    retry_after = int(exc.response.headers.get("Retry-After", "60"))
                    log.warning("slack.rate_limited", method=method, retry_after=retry_after)
                    raise SlackRateLimitError(
                        f"Slack Rate-Limit. Retry nach {retry_after}s"
                    ) from exc

                log.error("slack.api_error", method=method, error_code=error_code)
                raise SlackAPIError(f"Slack-API-Fehler in {method}: {error_code}") from exc

        except ImportError:
            pass

        log.error("slack.unexpected_error", method=method, error=str(exc))
        raise SlackAPIError(f"Unerwarteter Slack-Fehler in {method}: {exc}") from exc


# ── Singleton ──────────────────────────────────────────────────────────────────

_slack_client: SlackClient | None = None


def get_slack_client() -> SlackClient:
    """Gecachte SlackClient-Instanz."""
    global _slack_client
    if _slack_client is None:
        _slack_client = SlackClient()
    return _slack_client
