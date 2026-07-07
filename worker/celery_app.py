import os

from celery import Celery

from api.constants import REDIS_URL as _REDIS_URL_DEFAULT

redis_url = os.environ.get("REDIS_URL", _REDIS_URL_DEFAULT)
result_backend = redis_url.replace("/0", "/1")

# Ingestion time limits are a *backstop*, not the primary guard. The real bounds are
# per-unit and progress-based: each embed request times out in the adapter, and a job
# that stops making progress is caught by its heartbeat (worker/tasks.py). So a long
# job that keeps embedding chunks is never killed for taking a while — these limits
# only catch a worker wedged in an opaque C call. Generous default (a 1400-page PDF on
# CPU is ~15-40 min of steady progress); raise via env for very large corpora.
_HARD_LIMIT = int(os.environ.get("CELERY_TASK_TIME_LIMIT", "14460"))      # 4h + 1m
_SOFT_LIMIT = int(os.environ.get("CELERY_TASK_SOFT_TIME_LIMIT", "14400"))  # 4h backstop

celery_app = Celery(
    "embedbase",
    broker=redis_url,
    backend=result_backend,
    include=["worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,           # re-queue on worker crash
    worker_prefetch_multiplier=1,  # one task at a time per worker process
    task_time_limit=_HARD_LIMIT,        # hard kill
    task_soft_time_limit=_SOFT_LIMIT,   # raises SoftTimeLimitExceeded first
    broker_connection_retry_on_startup=True,
)

# Periodic sweep purging temporary documents past their retention window (PR 4).
# Fired by Celery's *embedded* beat (the ``-B`` flag on the worker command in
# docker-compose.yml); the task itself is a cheap SELECT + fan-out to delete_document.
# ponytail: one embedded beat is safe with this single ``--concurrency=1`` worker. If
# the worker is ever scaled to >1 replica, split beat into its own one-replica service
# so the schedule isn't fired once per replica.
_PURGE_INTERVAL = float(os.environ.get("PURGE_INTERVAL_SECONDS", "300"))  # every 5 min
# Re-enqueue ingests that paused on an embedding-provider rate limit / quota, so a
# document partly embedded when the tier limit was hit keeps making progress within
# quota until every chunk is done (worker/tasks.py::retry_rate_limited_ingests).
_RETRY_INTERVAL = float(os.environ.get("RATE_LIMIT_RETRY_SECONDS", "300"))  # every 5 min

celery_app.conf.beat_schedule = {
    "purge-expired-documents": {
        "task": "worker.tasks.purge_expired_documents",
        "schedule": _PURGE_INTERVAL,
    },
    "retry-rate-limited-ingests": {
        "task": "worker.tasks.retry_rate_limited_ingests",
        "schedule": _RETRY_INTERVAL,
    },
}

# Side-effect import: registers the worker_process_init signal that starts the
# per-process config hot-reload listener. F401 is intentional — the module is
# imported for its decorator registration, not a referenced symbol.
from worker import config_reload  # noqa: F401,E402  signal registration on worker boot
