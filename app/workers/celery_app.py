# TODO: Celery App Konfiguration
# Spezifikation: docs/queue-system.md
#
# Queues: high, default, low
# Broker + Backend: Redis
# task_acks_late=True, worker_prefetch_multiplier=1 (kein Job-Verlust bei Absturz)
# Routing: process_dummy_job → high Queue

from celery import Celery
from app.config import settings

celery_app = Celery(
    "social_ai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

# TODO: celery_app.conf.update(...) mit vollständiger Konfiguration
