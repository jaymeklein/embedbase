import time

from fastapi import APIRouter

from api.dependencies import get_embedding_adapter
from api.settings import settings

router = APIRouter(tags=["system"])

_start_time = time.time()


@router.get("/healthz")
async def healthz():
    embedding_adapter = await get_embedding_adapter()
    return {
        "status": "ok",
        "service": "api",
        "version": "1.0.0",
        "vector_store": settings.vector_store,
        "vector_store_connected": True,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_model_loaded": embedding_adapter is not None,
        "uptime_seconds": int(time.time() - _start_time),
    }


@router.get("/metrics")
async def metrics():
    # Full Prometheus metrics implemented in Delivery 6
    return {"status": "metrics endpoint — full implementation in Delivery 6"}
