import time
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.api.routes.health import router as health_router
from app.api.routes.slack import router as slack_router

log = structlog.get_logger(__name__)


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup- und Shutdown-Hooks."""

    # Startup
    log.info(
        "app.startup",
        version=settings.APP_VERSION,
        env=settings.APP_ENV,
        debug=settings.DEBUG,
    )

    # Konfigurationswarnungen ausgeben (blockiert nicht)
    for warning in settings.warn_missing():
        log.warning("config.warning", message=warning)

    yield

    # Shutdown
    log.info("app.shutdown")


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="3D Memories Social AI",
    description="Agentisches Social-Media-Produktionssystem für 3D Memories.",
    version=settings.APP_VERSION,
    lifespan=lifespan,
    # Swagger nur in Development — nie in Production exponieren
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    openapi_url="/openapi.json" if settings.DEBUG else None,
)


# ── Middleware ─────────────────────────────────────────────────────────────────

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """
    Hängt jedem Request eine eindeutige ID an.
    X-Request-ID: aus eingehendem Header übernehmen oder neu generieren.
    Wird in Logs und Antwort-Headern mitgeführt.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    start = time.monotonic()

    # Request-ID in structlog-Kontext einbinden
    with structlog.contextvars.bound_contextvars(request_id=request_id):
        response = await call_next(request)

    latency_ms = round((time.monotonic() - start) * 1000, 1)
    response.headers["X-Request-ID"] = request_id

    log.info(
        "http.request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        latency_ms=latency_ms,
    )

    return response


# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(health_router)
app.include_router(slack_router)


# ── Exception Handler ──────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Fängt alle unbehandelten Exceptions ab.
    Gibt 500 zurück ohne interne Details zu exponieren.
    Stack-Trace nur im Log, nie in der HTTP-Antwort.
    """
    log.error(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        exc_type=type(exc).__name__,
        error=str(exc),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Interner Serverfehler"},
    )
