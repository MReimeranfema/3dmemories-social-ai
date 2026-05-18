"""
Celery Tasks — Hintergrundverarbeitung.
Spezifikation: docs/data-flow.md, docs/job-lifecycle.md

Alle Tasks sind idempotent: mehrfaches Ausführen darf nicht zu Duplikaten führen.
Retry-Strategie: max. 2 Retries, Backoff 30s / 90s.

Queue-Zuordnung:
  high:    Ideen-Freigabe, kritische Status-Updates
  default: Ideen-Generierung, Content-Jobs
  low:     Sheets-Sync, Logging, Cleanup
"""

import structlog
from celery import shared_task

from app.workers.celery_app import celery_app

log = structlog.get_logger(__name__)


# ── Task: Ideen-Agent starten ──────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.workers.tasks.run_ideen_agent",
    queue="default",
    max_retries=2,
    default_retry_delay=30,
)
def run_ideen_agent(
    self,
    platform: str = "all",
    count: int = 10,
    focus: str | None = None,
    model: str = "haiku",
    channel: str = "",
    test_mode: bool = False,
) -> dict:
    """
    Startet den Ideen-Agenten als Celery-Background-Task.

    Wird aufgerufen durch:
      - /neue-ideen Slash Command (slack.py)
      - Geplanten Tagesstart (Phase 2: Celery Beat)
      - Orchestrator wenn Refill-Threshold unterschritten

    Args:
        platform:  'instagram' | 'facebook' | 'tiktok' | 'all'
        count:     Anzahl gewünschter Ideen (1–20)
        focus:     Optionaler Themen-Fokus
        model:     'haiku' | 'sonnet'
        channel:   Slack-Channel für Output
        test_mode: True = kein echter DB-/Slack-Call

    Returns:
        Dict mit IdeenAgentResult-Feldern (JSON-serialisierbar)
    """
    log.info(
        "task.run_ideen_agent.start",
        task_id=self.request.id,
        platform=platform,
        count=count,
        focus=focus,
        model=model,
        test_mode=test_mode,
    )

    try:
        from app.agents.ideen_agent import IdeenAgent, IdeenAgentParams

        # DB-Session (nur wenn nicht test_mode)
        db = None
        if not test_mode:
            from app.database import SessionLocal  # type: ignore
            db = SessionLocal()

        try:
            agent = IdeenAgent(db=db, test_mode=test_mode)
            params = IdeenAgentParams(
                platform=platform,
                count=count,
                focus=focus,
                model=model,
                channel=channel,
                test_mode=test_mode,
            )
            result = agent.run(params)

            log.info(
                "task.run_ideen_agent.complete",
                task_id=self.request.id,
                batch_id=result.batch_id,
                ideas_passed=result.ideas_passed_selfcheck,
                cost_eur=result.cost_eur,
                slack_posted=result.slack_posted,
            )

            return {
                "batch_id": result.batch_id,
                "ideas_requested": result.ideas_requested,
                "ideas_generated": result.ideas_generated,
                "ideas_passed_selfcheck": result.ideas_passed_selfcheck,
                "idea_ids": result.idea_ids,
                "cost_eur": result.cost_eur,
                "model_used": result.model_used,
                "slack_posted": result.slack_posted,
                "error": result.error,
            }

        finally:
            if db is not None:
                db.close()

    except Exception as exc:
        log.error(
            "task.run_ideen_agent.failed",
            task_id=self.request.id,
            error=str(exc),
            retries=self.request.retries,
            max_retries=self.max_retries,
        )
        # Retry mit Backoff: 30s → 90s
        retry_delays = [30, 90]
        delay = retry_delays[min(self.request.retries, len(retry_delays) - 1)]

        raise self.retry(exc=exc, countdown=delay)


# ── Task: Idee freigeben ───────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.workers.tasks.handle_approve_idea",
    queue="high",
    max_retries=2,
    default_retry_delay=10,
)
def handle_approve_idea(
    self,
    idea_id: str,
    user_id: str,
    channel: str,
    message_ts: str,
) -> dict:
    """
    Verarbeitet Freigabe einer Idee (Button [FREIGEBEN]).

    Wird aufgerufen aus slack.py beim approve_idea Block-Action.

    Args:
        idea_id:    Ideen-ID (z.B. '3DM-2026-042')
        user_id:    Slack User-ID des freigebenden Users
        channel:    Channel-ID der Ideen-Karte
        message_ts: Timestamp der Ideen-Karten-Nachricht (für Update)
    """
    log.info(
        "task.handle_approve_idea.start",
        idea_id=idea_id,
        user_id=user_id,
    )

    try:
        from app.agents.ideen_agent import IdeenAgent

        db = None
        try:
            from app.database import SessionLocal  # type: ignore
            db = SessionLocal()
        except Exception:
            pass  # Kein DB in Dev-Mode

        agent = IdeenAgent(db=db)
        agent.handle_approve(
            idea_id=idea_id,
            user_id=user_id,
            channel=channel,
            message_ts=message_ts,
        )

        if db:
            db.close()

        log.info("task.handle_approve_idea.complete", idea_id=idea_id)
        return {"status": "approved", "idea_id": idea_id}

    except Exception as exc:
        log.error("task.handle_approve_idea.failed", idea_id=idea_id, error=str(exc))
        raise self.retry(exc=exc, countdown=10)


# ── Task: Idee ablehnen ────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.workers.tasks.handle_reject_idea",
    queue="high",
    max_retries=2,
    default_retry_delay=10,
)
def handle_reject_idea(
    self,
    idea_id: str,
    user_id: str,
    channel: str,
    message_ts: str,
) -> dict:
    """
    Verarbeitet Ablehnung einer Idee (Button [ABLEHNEN]).

    Returns:
        Dict mit open_ideas (Anzahl noch offener Ideen)
    """
    log.info(
        "task.handle_reject_idea.start",
        idea_id=idea_id,
        user_id=user_id,
    )

    try:
        from app.agents.ideen_agent import IdeenAgent

        db = None
        try:
            from app.database import SessionLocal  # type: ignore
            db = SessionLocal()
        except Exception:
            pass

        agent = IdeenAgent(db=db)
        open_count = agent.handle_reject(
            idea_id=idea_id,
            user_id=user_id,
            channel=channel,
            message_ts=message_ts,
        )

        if db:
            db.close()

        log.info("task.handle_reject_idea.complete", idea_id=idea_id, open_ideas=open_count)
        return {"status": "rejected", "idea_id": idea_id, "open_ideas": open_count}

    except Exception as exc:
        log.error("task.handle_reject_idea.failed", idea_id=idea_id, error=str(exc))
        raise self.retry(exc=exc, countdown=10)
