import time
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

log = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])

# Startzeit der App — für Uptime-Berechnung
_APP_START = time.monotonic()


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """
    Vollständiger Health-Check.

    Prüft:
    - Datenbankverbindung (SELECT 1)
    - Redis-Verbindung (PING)
    - Konfigurationswarnungen

    Antwortet mit HTTP 200 wenn alle Kernkomponenten erreichbar sind,
    HTTP 503 wenn die Datenbank nicht erreichbar ist.
    """
    results: dict = {
        "status": "ok",
        "version": settings.APP_VERSION,
        "env": settings.APP_ENV,
        "uptime_seconds": round(time.monotonic() - _APP_START, 1),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {},
    }

    # ── Datenbank-Check ────────────────────────────────────────────────────────
    try:
        db.execute(text("SELECT 1"))
        results["checks"]["database"] = {"status": "ok"}
    except Exception as e:
        log.error("health.db.error", error=str(e))
        results["checks"]["database"] = {"status": "error", "detail": "Verbindung fehlgeschlagen"}
        results["status"] = "degraded"

    # ── Redis-Check ────────────────────────────────────────────────────────────
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.ping()
        results["checks"]["redis"] = {"status": "ok"}
    except Exception as e:
        log.warning("health.redis.error", error=str(e))
        results["checks"]["redis"] = {"status": "error", "detail": "Verbindung fehlgeschlagen"}
        # Redis-Ausfall ist kritisch (Celery läuft nicht) → degraded
        results["status"] = "degraded"

    # ── Konfigurationswarnungen ────────────────────────────────────────────────
    warnings = settings.warn_missing()
    if warnings:
        results["warnings"] = warnings

    status_code = 200 if results["status"] == "ok" else 503
    log.info("health.check", status=results["status"], status_code=status_code)
    return JSONResponse(content=results, status_code=status_code)


@router.get("/health/ready")
def readiness():
    """
    Kubernetes / Docker readiness probe.
    Antwortet immer mit 200 sobald der Prozess läuft.
    Kein DB-Check — nur: ist der Prozess am Leben?
    """
    return {"ready": True, "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/health/live")
def liveness():
    """
    Kubernetes / Docker liveness probe.
    Identisch mit readiness — separater Endpunkt für explizite K8s-Konfiguration.
    """
    return {"alive": True}
