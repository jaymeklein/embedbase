import asyncio

from fastapi import APIRouter, Depends

from api.models.config import AppConfig
from api.services import config_service
from api.services.auth import require_master

# Config carries secrets and mutates runtime state — master key required.
router = APIRouter(prefix="/config", tags=["config"], dependencies=[Depends(require_master)])


@router.get("")
async def get_config():
    return config_service.get_masked_config()


@router.put("")
async def update_config(payload: AppConfig):
    # Off the event loop: building adapters can load the embedding model (blocking).
    return await asyncio.to_thread(config_service.apply_config, payload)


@router.get("/ollama-models")
async def list_ollama_models(base_url: str | None = None):
    # Off the event loop: querying Ollama is a blocking HTTP call.
    return await asyncio.to_thread(config_service.list_ollama_models, base_url)


@router.get("/reload-status/{version_id}")
async def get_reload_status(version_id: str):
    return config_service.get_reload_status(version_id)
