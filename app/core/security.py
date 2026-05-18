"""
Slack Sicherheits-Layer.
Spezifikation: docs/security-concept.md — Abschnitt 3 + 6.

Zwei Aufgaben:
  1. verify_slack_signature — HMAC-SHA256-Verifikation als FastAPI-Dependency
  2. check_authorized_user  — User-ID gegen Whitelist prüfen
"""

import hashlib
import hmac
import time

import structlog
from fastapi import HTTPException, Request

from app.config import settings

log = structlog.get_logger(__name__)


# ── HMAC-SHA256 Signatur-Verifikation ──────────────────────────────────────────

async def verify_slack_signature(request: Request) -> None:
    """
    FastAPI-Dependency — wird vor JEDEM /slack/-Endpunkt ausgeführt.

    Prüfreihenfolge:
      1. Pflicht-Header vorhanden (X-Slack-Signature, X-Slack-Request-Timestamp)
      2. Timestamp-Prüfung: max. 5 Minuten alt (Replay-Schutz)
      3. HMAC-SHA256-Signatur berechnen und vergleichen (timing-safe)

    Wirft HTTP 403 bei jedem Fehlschlag.
    Gibt nichts zurück bei Erfolg.

    Hinweis Development: Wenn SLACK_SIGNING_SECRET leer ist, wird Verifikation
    übersprungen und eine Warnung geloggt. Nie in Production so lassen.
    """
    # Dev-Mode: Secret nicht gesetzt → Verifikation überspringen
    if not settings.SLACK_SIGNING_SECRET:
        log.warning(
            "slack.signature.skipped",
            reason="SLACK_SIGNING_SECRET nicht gesetzt — nur in Development erlaubt",
        )
        return

    # ── 1. Rohen Body lesen (wird von Starlette gecacht — keine doppelte Arbeit) ─
    body = await request.body()

    # ── 2. Pflicht-Header prüfen ───────────────────────────────────────────────
    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    signature = request.headers.get("X-Slack-Signature")

    if not timestamp or not signature:
        _log_security_event("missing_slack_headers", request)
        raise HTTPException(status_code=403, detail="Missing Slack headers")

    # ── 3. Replay-Attack-Schutz: max. 5 Minuten alt ───────────────────────────
    try:
        request_age = abs(time.time() - int(timestamp))
    except (ValueError, TypeError):
        _log_security_event("invalid_slack_timestamp", request, detail=timestamp)
        raise HTTPException(status_code=403, detail="Invalid timestamp")

    if request_age > 300:
        _log_security_event("slack_replay_attack", request, detail=f"age={request_age:.0f}s")
        raise HTTPException(status_code=403, detail="Request too old")

    # ── 4. Erwartete Signatur berechnen ────────────────────────────────────────
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected_sig = "v0=" + hmac.new(
        settings.SLACK_SIGNING_SECRET.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # ── 5. Timing-sicherer Vergleich ───────────────────────────────────────────
    if not hmac.compare_digest(expected_sig, signature):
        _log_security_event("slack_signature_mismatch", request)
        raise HTTPException(status_code=403, detail="Invalid signature")

    log.debug("slack.signature.ok", path=request.url.path)


# ── User-Autorisierung ─────────────────────────────────────────────────────────

def check_authorized_user(user_id: str) -> bool:
    """
    Prüft ob eine Slack-User-ID in der Whitelist steht.

    Gibt True zurück wenn berechtigt, False wenn nicht.
    Loggt unbefugte Versuche als Security-Event.

    Wenn AUTHORIZED_SLACK_USERS leer ist und APP_ENV != 'production':
      → alle User erlaubt (Development-Komfort)
    In Production ohne Whitelist: alle User blockiert + kritisches Log.
    """
    authorized = settings.authorized_users_list

    # Production ohne Whitelist = harter Block
    if not authorized and settings.is_production:
        log.error(
            "security.no_whitelist_in_production",
            user_id=user_id,
        )
        return False

    # Dev-Mode ohne Whitelist = alle erlaubt
    if not authorized:
        log.warning(
            "slack.auth.whitelist_empty",
            user_id=user_id,
            reason="AUTHORIZED_SLACK_USERS leer — alle User erlaubt (nur Development)",
        )
        return True

    is_authorized = user_id in authorized

    if not is_authorized:
        log.warning(
            "security.unauthorized_slack_user",
            user_id=user_id,
            authorized_count=len(authorized),
        )

    return is_authorized


# ── Internes Security-Logging ──────────────────────────────────────────────────

def _log_security_event(event_type: str, request: Request, detail: str = "") -> None:
    """
    Sicherheitsereignisse mit WARNING-Level loggen.
    Remote-IP aus X-Real-IP (via Nginx gesetzt) oder fallback auf direkte IP.
    """
    log.warning(
        "security.event",
        event_type=event_type,
        remote_ip=request.headers.get("X-Real-IP", "unknown"),
        user_agent=request.headers.get("User-Agent", "")[:100],
        path=request.url.path,
        detail=detail,
    )
