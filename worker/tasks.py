import structlog
from celery.exceptions import SoftTimeLimitExceeded
from worker.celery_app import celery_app

logger = structlog.get_logger()


@celery_app.task(
    bind=True,
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=60,
)
def ingest_document(self, job_id: str, file_path: str, collection_id: str, file_type: str):
    """
    Parse → chunk → embed → store a single document.
    Full implementation in Delivery 2.
    """
    # SoftTimeLimitExceeded MUST be the first except clause — it's a subclass of Exception
    try:
        raise NotImplementedError("ingest_document implemented in Delivery 2")
    except SoftTimeLimitExceeded:
        logger.warning("task exceeded time limit", job_id=job_id)
        raise  # plain raise — never self.retry()
    except Exception as exc:
        logger.error("ingest task failed", job_id=job_id, error=str(exc))
        raise self.retry(exc=exc)


@celery_app.task(bind=True)
def delete_document(self, document_id: str, collection_id: str):
    """
    Remove vectors + BM25 corpus entries for a document.
    Full implementation in Delivery 3.
    """
    try:
        raise NotImplementedError("delete_document implemented in Delivery 3")
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:
        logger.error("delete task failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc)
