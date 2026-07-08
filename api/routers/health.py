from fastapi import APIRouter

from api.dependencies import (
    get_app_config,
    get_embedding_adapter,
    get_reranker,
    get_vector_store,
)
from api.services.health import build_health

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def healthz():
    return await build_health(
        get_vector_store(),
        get_embedding_adapter(),
        get_app_config(),
        reranker_loaded=get_reranker() is not None,
    )


@router.get("/metrics")
async def metrics():
    # Full Prometheus metrics implemented in Delivery 6
    return {"status": "metrics endpoint — full implementation in Delivery 6"}
