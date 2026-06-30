import os

from celery import Celery

from api.constants import REDIS_URL as _REDIS_URL_DEFAULT

redis_url = os.environ.get("REDIS_URL", _REDIS_URL_DEFAULT)
result_backend = redis_url.replace("/0", "/1")

# Ingestion time limits. Defaults fit CPU-bound docling on large PDFs (layout +
# table inference runs minutes/doc); the old 9-min limit killed those mid-convert.
# Lower them for a GPU/pymupdf-only deploy. STALE_PROCESSING_SECONDS in tasks.py
# tracks the hard limit, so a reclaimed task is genuinely dead, not just slow.
_HARD_LIMIT = int(os.environ.get("CELERY_TASK_TIME_LIMIT", "1860"))      # 31 min
_SOFT_LIMIT = int(os.environ.get("CELERY_TASK_SOFT_TIME_LIMIT", "1800"))  # 30 min

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

# Side-effect import: registers the worker_process_init signal that starts the
# per-process config hot-reload listener. F401 is intentional — the module is
# imported for its decorator registration, not a referenced symbol.
from worker import config_reload  # noqa: F401,E402  signal registration on worker boot
