from fastapi import APIRouter

from api.dependencies import get_embedding_adapter, get_vector_store
from api.services.health import build_health

router = APIRouter(tags=["system"])


@router.get("/healthz")
async def healthz():
    return await build_health(get_vector_store(), get_embedding_adapter())


@router.get("/metrics")
async def metrics():
    # Full Prometheus metrics implemented in Delivery 6
    return {"status": "metrics endpoint — full implementation in Delivery 6"}
