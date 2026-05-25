from fastapi import APIRouter, HTTPException
from api.models.config import AppConfig

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def get_config():
    # Full implementation in Delivery 6
    raise HTTPException(501, "Config endpoint implemented in Delivery 6")


@router.put("")
async def update_config(config: AppConfig):
    raise HTTPException(501, "Config reload implemented in Delivery 6")


@router.get("/reload-status/{version_id}")
async def get_reload_status(version_id: str):
    raise HTTPException(501, "Config reload status implemented in Delivery 6")
