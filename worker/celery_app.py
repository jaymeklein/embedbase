import os

from celery import Celery

from api.constants import REDIS_URL as _REDIS_URL_DEFAULT

redis_url = os.environ.get("REDIS_URL", _REDIS_URL_DEFAULT)
result_backend = redis_url.replace("/0", "/1")

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
    task_time_limit=600,           # 10 min hard limit
    task_soft_time_limit=540,      # 9 min soft limit (raises SoftTimeLimitExceeded)
    broker_connection_retry_on_startup=True,
)
