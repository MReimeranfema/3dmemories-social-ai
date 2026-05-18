"""
Slack-Endpunkte — Events, Actions, Commands.
Spezifikation: docs/slack-event-flow.md

DREI Regeln die immer gelten:
  1. HMAC-Verifikation VOR jeder Verarbeitung (verify_slack_signature-Dependency)
  2. Innerhalb von 3 Sekunden HTTP 200 an Slack zurück
  3. Eigentliche Verarbeitung als BackgroundTask (non-blocking)

Alle Handler haben aktuell Dummy-Logik. Markierungen TODO: kennzeichnen
wo die echte Celery-Queue-Einbindung hinkommt.
"""

import json
import re
import urllib.parse

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.core.security import check_authorized_user, verify_slack_signature

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])


# ── Form-Parser (Body-safe) ────────────────────────────────────────────────────

async def _parse_form(request: Request) -> dict[str, str]:
    """
    Parst URL-encoded Form-Daten aus dem gecachten Request-Body.

    Hintergrund: verify_slack_signature liest den Body via request.body()
    (Starlette cached diesen). request.form() liest den Stream erneut und
    schlägt fehl. Lösung: nochmaliger request.body()-Aufruf liefert den Cache,
    urllib.parse.parse_qsl erledigt das Parsing ohne Starlette-Form-Mechanismus.
    """
    body = await request.body()  # gecachter Bytes-Aufruf — kein zweiter HTTP-Read
    return dict(urllib.parse.parse_qsl(body.decode("utf-8")))


# ── POST /slack/events ─────────────────────────────────────────────────────────

@router.post("/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_slack_signature),
):
    """
    Empfängt Slack Event Subscriptions.

    Verarbeitete Event-Typen:
      url_verification — Slack-Endpoint-Verifikation beim Setup (muss synchron antworten)
      app_mention      — Bot wird in Nachricht erwähnt
      message.channels — Nachrichten in Channels (optional)

    Alles andere: HTTP 200, ignoriert.
    """
    body = await request.json()
    event_type = body.get("type")

    # ── url_verification — muss synchron antworten ─────────────────────────────
    # Slack sendet challenge beim ersten Setup des Endpoints.
    # Muss mit {"challenge": "<wert>"} antworten — keine Async-Verarbeitung.
    if event_type == "url_verification":
        challenge = body.get("challenge", "")
        log.info("slack.events.url_verification", challenge=challenge[:10] + "...")
        return JSONResponse(content={"challenge": challenge})

    # ── Alle anderen Events ────────────────────────────────────────────────────
    event = body.get("event", {})
    actual_event_type = event.get("type", "unknown")

    log.info(
        "slack.events.received",
        event_type=actual_event_type,
        team_id=body.get("team_id"),
    )

    match actual_event_type:
        case "app_mention":
            background_tasks.add_task(_handle_app_mention, event)
        case "message":
            # Nur Channel-Nachrichten — Bot-eigene Nachrichten ignorieren
            if event.get("bot_id"):
                pass
            else:
                background_tasks.add_task(_handle_channel_message, event)
        case _:
            log.debug("slack.events.ignored", event_type=actual_event_type)

    # Slack erwartet immer HTTP 200 — sofort zurückgeben
    return Response(status_code=200)


# ── POST /slack/actions ────────────────────────────────────────────────────────

@router.post("/actions")
async def slack_actions(
    request: Request,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_slack_signature),
):
    """
    Empfängt Interactive Components (Button-Klicks, Modal-Submissions).

    Slack sendet URL-encoded Form-Daten mit einem JSON-String im Feld 'payload'.

    Verarbeitete Interaction-Typen:
      block_actions   — Button-Klick, Select-Menu
      view_submission — Modal-Formular abgesendet
      view_closed     — Modal geschlossen ohne Absenden

    Bekannte action_ids (Dummy-Implementierung):
      approve_idea, reject_idea
      approve_content, reject_content, request_revision
      manual_retry, discard_job, qc_override
      show_error_detail, manual_resume
    """
    form = await _parse_form(request)
    raw_payload = form.get("payload", "")

    if not raw_payload:
        log.warning("slack.actions.missing_payload")
        return Response(status_code=200)

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        log.error("slack.actions.invalid_json")
        return Response(status_code=200)

    interaction_type = payload.get("type")
    user_id = payload.get("user", {}).get("id", "")

    log.info("slack.actions.received", interaction_type=interaction_type, user_id=user_id)

    # ── User-Autorisierung ─────────────────────────────────────────────────────
    if not check_authorized_user(user_id):
        background_tasks.add_task(
            _send_unauthorized_ephemeral,
            channel_id=payload.get("channel", {}).get("id", ""),
            user_id=user_id,
        )
        return Response(status_code=200)

    # ── Dispatch nach Interaction-Typ ──────────────────────────────────────────
    match interaction_type:
        case "block_actions":
            background_tasks.add_task(_handle_block_action, payload)
        case "view_submission":
            background_tasks.add_task(_handle_view_submission, payload)
        case "view_closed":
            log.info("slack.actions.view_closed", callback_id=payload.get("view", {}).get("callback_id"))
        case _:
            log.warning("slack.actions.unknown_type", interaction_type=interaction_type)

    return Response(status_code=200)


# ── POST /slack/commands ───────────────────────────────────────────────────────

@router.post("/commands")
async def slack_commands(
    request: Request,
    background_tasks: BackgroundTasks,
    _: None = Depends(verify_slack_signature),
):
    """
    Empfängt Slash Commands.

    Slack sendet URL-encoded Form-Daten mit Feldern:
      command, text, user_id, user_name, channel_id, channel_name

    Implementierte Commands:
      /neue-ideen    — Ideen-Batch anfordern
      /job-status    — Status eines Content-Jobs
      /system-status — Systemzustand
      /kosten        — Kostenübersicht
      /hard-stop     — System sofort stoppen
      /resume        — System nach Hard Stop fortsetzen
      /activate-video — Video-Generierung aktivieren (Phase 2)

    Sofortige Text-Antwort (ephemeral) — Verarbeitung als BackgroundTask.
    """
    form = await _parse_form(request)
    command = form.get("command", "")
    text = form.get("text", "").strip()
    user_id = form.get("user_id", "")
    user_name = form.get("user_name", "")
    channel_id = form.get("channel_id", "")

    log.info("slack.commands.received", command=command, user_id=user_id, text=text[:50])

    # ── User-Autorisierung ─────────────────────────────────────────────────────
    if not check_authorized_user(user_id):
        return JSONResponse(content={
            "response_type": "ephemeral",
            "text": "Du bist nicht berechtigt, diesen Befehl auszuführen.",
        })

    # ── Command-Dispatch ───────────────────────────────────────────────────────
    match command:
        case "/neue-ideen":
            params = _parse_neue_ideen_command(text)
            background_tasks.add_task(_handle_neue_ideen, params, channel_id, user_id)
            platform_label = params["platform"].title()
            count_label = params["count"]
            focus_label = f" · Fokus: _{params['focus']}_" if params.get("focus") else ""
            return JSONResponse(content={
                "response_type": "ephemeral",
                "text": (
                    f":bulb: *Ideen-Agent gestartet* — generiere *{count_label} Ideen* "
                    f"für *{platform_label}*{focus_label}. Karten folgen in Kürze."
                ),
            })

        case "/job-status":
            if not text:
                return JSONResponse(content={
                    "response_type": "ephemeral",
                    "text": "Verwendung: `/job-status IG-2026-001`",
                })
            job_id = text.strip().upper()
            background_tasks.add_task(_handle_job_status, job_id, channel_id, user_id)
            return JSONResponse(content={
                "response_type": "ephemeral",
                "text": f"Suche Job `{job_id}`...",
            })

        case "/system-status":
            background_tasks.add_task(_handle_system_status, channel_id, user_id)
            return JSONResponse(content={
                "response_type": "ephemeral",
                "text": "Systemstatus wird abgerufen...",
            })

        case "/kosten":
            background_tasks.add_task(_handle_kosten, channel_id, user_id)
            return JSONResponse(content={
                "response_type": "ephemeral",
                "text": "Kostenübersicht wird erstellt...",
            })

        case "/hard-stop":
            log.warning("slack.commands.hard_stop", user_id=user_id, user_name=user_name)
            background_tasks.add_task(_handle_hard_stop, channel_id, user_id)
            return JSONResponse(content={
                "response_type": "in_channel",
                "text": f"*HARD STOP* ausgelöst von @{user_name}. Alle laufenden Jobs werden gestoppt.",
            })

        case "/resume":
            background_tasks.add_task(_handle_resume, channel_id, user_id)
            return JSONResponse(content={
                "response_type": "ephemeral",
                "text": "System-Resume wird eingeleitet...",
            })

        case "/activate-video":
            return JSONResponse(content={
                "response_type": "ephemeral",
                "text": "Video-Generierung ist in Phase 1 noch nicht verfügbar.",
            })

        case _:
            log.warning("slack.commands.unknown", command=command)
            return JSONResponse(content={
                "response_type": "ephemeral",
                "text": f"Unbekannter Befehl: `{command}`",
            })


# ── Background-Handler: Events ─────────────────────────────────────────────────

def _handle_app_mention(event: dict) -> None:
    """Reagiert auf @-Erwähnung des Bots. Dummy: nur loggen."""
    user_id = event.get("user", "")
    text = event.get("text", "")
    channel = event.get("channel", "")
    log.info("slack.event.app_mention", user_id=user_id, channel=channel, text=text[:80])
    # TODO: Celery-Task enqueuen — z.B. Hilfe-Text zurücksenden


def _handle_channel_message(event: dict) -> None:
    """Channel-Nachricht empfangen. Dummy: nur loggen."""
    log.info("slack.event.message", channel=event.get("channel"), user=event.get("user"))
    # TODO: Celery-Task enqueuen wenn Nachricht relevant ist


# ── Background-Handler: Actions ────────────────────────────────────────────────

def _handle_block_action(payload: dict) -> None:
    """
    Dispatcht Button-Klicks nach action_id.
    Läuft bereits als BackgroundTask — ruft Handler direkt auf (kein weiteres Nesting).
    """
    actions = payload.get("actions", [])
    if not actions:
        log.warning("slack.action.no_actions_in_payload")
        return

    action = actions[0]
    action_id = action.get("action_id", "")
    raw_value = action.get("value", "{}")
    user_id = payload.get("user", {}).get("id", "")
    channel_id = payload.get("channel", {}).get("id", "")
    message_ts = payload.get("message", {}).get("ts", "")

    try:
        value = json.loads(raw_value) if raw_value.startswith("{") else {"raw": raw_value}
    except json.JSONDecodeError:
        value = {"raw": raw_value}

    log.info("slack.action.dispatch", action_id=action_id, user_id=user_id, value=value)

    match action_id:
        case "approve_idea":
            idea_id = value.get("idea_id", "")
            log.info("slack.action.approve_idea", idea_id=idea_id, user_id=user_id)
            _handle_approve_idea_bg(idea_id, user_id, channel_id, message_ts)

        case "reject_idea":
            idea_id = value.get("idea_id", "")
            log.info("slack.action.reject_idea", idea_id=idea_id, user_id=user_id)
            _handle_reject_idea_bg(idea_id, user_id, channel_id, message_ts)

        case "show_idea_detail":
            idea_id = value.get("idea_id", "")
            log.info("slack.action.show_idea_detail", idea_id=idea_id, user_id=user_id)
            _handle_show_idea_detail_bg(idea_id, user_id, channel_id)

        case "approve_content":
            content_id = value.get("content_id", "")
            log.info("slack.action.approve_content", content_id=content_id, user_id=user_id)
            # TODO: Celery high-Queue → handle_approve_content(content_id, user_id)

        case "reject_content":
            content_id = value.get("content_id", "")
            log.info("slack.action.reject_content", content_id=content_id, user_id=user_id)
            # TODO: Celery high-Queue → handle_reject_content(content_id, user_id)

        case "request_revision":
            content_id = value.get("content_id", "")
            log.info("slack.action.request_revision", content_id=content_id, user_id=user_id)
            # TODO: Slack views.open → Revision-Modal öffnen

        case "manual_retry":
            content_id = value.get("content_id", "")
            error_id = value.get("error_id", "")
            log.info("slack.action.manual_retry", content_id=content_id, error_id=error_id)
            # TODO: Celery → handle_manual_retry(content_id, error_id, user_id)

        case "discard_job":
            content_id = value.get("content_id", "")
            log.info("slack.action.discard_job", content_id=content_id, user_id=user_id)
            # TODO: Celery → archive_job(content_id, reason="manual_discard")

        case "qc_override":
            content_id = value.get("content_id", "")
            log.warning("slack.action.qc_override", content_id=content_id, user_id=user_id)
            # TODO: Celery → handle_qc_override(content_id, user_id)

        case "show_error_detail":
            error_id = value.get("error_id", "")
            log.info("slack.action.show_error_detail", error_id=error_id)
            # TODO: Slack views.open → Error-Detail-Modal

        case "manual_resume":
            error_id = value.get("error_id", "")
            log.info("slack.action.manual_resume", error_id=error_id, user_id=user_id)
            # TODO: Celery → handle_manual_resume(error_id, user_id)

        case _:
            log.warning("slack.action.unknown_action_id", action_id=action_id)


def _handle_view_submission(payload: dict) -> None:
    """Modal-Submission. Dummy: nur loggen."""
    callback_id = payload.get("view", {}).get("callback_id", "")
    user_id = payload.get("user", {}).get("id", "")
    values = payload.get("view", {}).get("state", {}).get("values", {})

    log.info("slack.view_submission", callback_id=callback_id, user_id=user_id)

    match callback_id:
        case "revision_modal":
            # Revisionstext aus Modal-Werten extrahieren
            revision_text = ""
            for block_values in values.values():
                for element_values in block_values.values():
                    revision_text = element_values.get("value", "")
            log.info("slack.revision_modal.submitted", revision_text=revision_text[:100])
            # TODO: Celery → handle_revision_submission(content_id, revision_text, user_id)
        case _:
            log.warning("slack.view_submission.unknown_callback", callback_id=callback_id)


# ── DB-Helper für Background-Tasks ────────────────────────────────────────────

def _get_bg_db():
    """
    Erstellt eine DB-Session für Background-Tasks.
    Gibt None zurück wenn DB nicht erreichbar (kein Hard-Fail).
    IdeenAgent fällt bei db=None automatisch in test_mode zurück.
    """
    try:
        from app.database import SessionLocal
        return SessionLocal()
    except Exception as exc:
        log.warning("slack.bg_task.db_unavailable", error=str(exc))
        return None


# ── Background-Handler: Commands ───────────────────────────────────────────────

def _handle_neue_ideen(params: dict, channel_id: str, user_id: str) -> None:
    """Startet den IdeenAgent als FastAPI-BackgroundTask mit echter DB-Session."""
    log.info(
        "slack.command.neue_ideen",
        platform=params["platform"],
        count=params["count"],
        focus=params.get("focus"),
        channel_id=channel_id,
    )
    db = _get_bg_db()
    try:
        from app.agents.ideen_agent import IdeenAgent, IdeenAgentParams
        agent = IdeenAgent(db=db)
        result = agent.run(IdeenAgentParams(
            platform=params["platform"],
            count=params["count"],
            focus=params.get("focus"),
            model="haiku",
            channel=channel_id,
        ))
        log.info(
            "slack.command.neue_ideen.done",
            batch_id=result.batch_id,
            ideas=result.ideas_passed_selfcheck,
            cost_eur=result.cost_eur,
        )
    except Exception as exc:
        log.error("slack.command.neue_ideen.error", error=str(exc))
    finally:
        if db is not None:
            db.close()


def _handle_job_status(job_id: str, channel_id: str, user_id: str) -> None:
    """Job-Status abfragen. Dummy: Platzhalter-Antwort."""
    log.info("slack.command.job_status", job_id=job_id)
    # TODO: DB-Abfrage → ContentJob.status → Slack-Antwort senden


def _handle_system_status(channel_id: str, user_id: str) -> None:
    """Systemstatus ausgeben. Dummy: nur loggen."""
    log.info("slack.command.system_status", channel_id=channel_id)
    # TODO: DB-Counts + Redis-Status + Worker-Status → Slack-Block-Message


def _handle_kosten(channel_id: str, user_id: str) -> None:
    """Kostenübersicht ausgeben. Dummy: nur loggen."""
    log.info("slack.command.kosten", channel_id=channel_id)
    # TODO: SELECT SUM(cost_eur) FROM costs WHERE ... → Slack-Block-Message


def _handle_hard_stop(channel_id: str, user_id: str) -> None:
    """System-Hard-Stop auslösen. Dummy: nur loggen."""
    log.warning("slack.command.hard_stop.executed", user_id=user_id)
    # TODO: Redis-Flag setzen → alle Worker-Tasks abbrechen → Slack-Bestätigung


def _handle_resume(channel_id: str, user_id: str) -> None:
    """System nach Hard-Stop fortsetzen. Dummy: nur loggen."""
    log.info("slack.command.resume.executed", user_id=user_id)
    # TODO: Redis-Hard-Stop-Flag löschen → Celery-Queue resumieren → Slack-Bestätigung


def _send_unauthorized_ephemeral(channel_id: str, user_id: str) -> None:
    """Ephemeral-Nachricht an unberechtigten User via SlackClient."""
    log.warning("slack.auth.sending_unauthorized_message", user_id=user_id)
    try:
        from app.services.slack_client import get_slack_client
        get_slack_client().post_ephemeral(
            channel=channel_id,
            user=user_id,
            text="Du bist nicht berechtigt, diese Aktion auszuführen.",
        )
    except Exception as exc:
        log.error("slack.auth.ephemeral_failed", error=str(exc))


# ── Button-Handler: Ideen-Agent ────────────────────────────────────────────────

def _handle_approve_idea_bg(
    idea_id: str, user_id: str, channel_id: str, message_ts: str
) -> None:
    """
    Handler für [FREIGEBEN]-Button.
    Erstellt eine echte DB-Session; fällt bei fehlendem DB auf test_mode zurück.
    """
    log.info("slack.action.approve_idea.bg", idea_id=idea_id, user_id=user_id)
    db = _get_bg_db()
    try:
        from app.agents.ideen_agent import IdeenAgent
        agent = IdeenAgent(db=db)
        agent.handle_approve(
            idea_id=idea_id,
            user_id=user_id,
            channel=channel_id,
            message_ts=message_ts,
        )
    except Exception as exc:
        log.error("slack.action.approve_idea.error", idea_id=idea_id, error=str(exc))
    finally:
        if db is not None:
            db.close()


def _handle_reject_idea_bg(
    idea_id: str, user_id: str, channel_id: str, message_ts: str
) -> None:
    """
    Handler für [ABLEHNEN]-Button.
    Erstellt eine echte DB-Session; fällt bei fehlendem DB auf test_mode zurück.
    """
    log.info("slack.action.reject_idea.bg", idea_id=idea_id, user_id=user_id)
    db = _get_bg_db()
    try:
        from app.agents.ideen_agent import IdeenAgent
        agent = IdeenAgent(db=db)
        agent.handle_reject(
            idea_id=idea_id,
            user_id=user_id,
            channel=channel_id,
            message_ts=message_ts,
        )
    except Exception as exc:
        log.error("slack.action.reject_idea.error", idea_id=idea_id, error=str(exc))
    finally:
        if db is not None:
            db.close()


def _handle_show_idea_detail_bg(idea_id: str, user_id: str, channel_id: str) -> None:
    """
    Handler für [DETAILS]-Button.
    Erstellt eine echte DB-Session; fällt bei fehlendem DB auf test_mode zurück.
    """
    log.info("slack.action.show_idea_detail.bg", idea_id=idea_id, user_id=user_id)
    db = _get_bg_db()
    try:
        from app.agents.ideen_agent import IdeenAgent
        agent = IdeenAgent(db=db)
        agent.handle_detail(idea_id=idea_id, user_id=user_id, channel=channel_id)
    except Exception as exc:
        log.error("slack.action.show_idea_detail.error", idea_id=idea_id, error=str(exc))
    finally:
        if db is not None:
            db.close()


# ── Command-Parser ─────────────────────────────────────────────────────────────

def _parse_neue_ideen_command(text: str) -> dict:
    """
    Parst: /neue-ideen [platform] [count] [--focus THEMA]

    Beispiele:
      (leer)                      → platform=all, count=10
      instagram                   → platform=instagram, count=10
      instagram 5                 → platform=instagram, count=5
      all 20 --focus Muttertag    → platform=all, count=20, focus="Muttertag"
      tiktok 3 --focus Haustiere  → platform=tiktok, count=3, focus="Haustiere"
    """
    VALID_PLATFORMS = {"instagram", "facebook", "tiktok", "pinterest", "youtube", "all"}
    params: dict = {"platform": "all", "count": 10, "focus": None}
    parts = text.strip().split()

    if not parts:
        return params

    # Platform (optionales erstes Wort)
    if parts[0].lower() in VALID_PLATFORMS:
        params["platform"] = parts[0].lower()
        parts = parts[1:]

    # Count (optionales nächstes Wort wenn Zahl)
    if parts and parts[0].isdigit():
        params["count"] = min(max(int(parts[0]), 1), 20)  # 1–20
        parts = parts[1:]

    # Focus (optional: --focus THEMA)
    if "--focus" in parts:
        focus_idx = parts.index("--focus")
        focus_words = parts[focus_idx + 1:]
        if focus_words:
            params["focus"] = " ".join(focus_words)[:100]  # max. 100 Zeichen

    return params
