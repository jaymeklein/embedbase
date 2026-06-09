from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from api.db import init_db
from api.dependencies import set_embedding_adapter, set_redis_client, set_vector_store
from api.middleware import RequestIDMiddleware, configure_logging
from api.models.config import AppConfig
from api.routers import collections, config, documents, health, mcp, search, workspaces
from api.settings import settings

logger = structlog.get_logger()


def _load_app_config() -> AppConfig:
    config_path = Path("/app/config.yaml")
    if not config_path.exists():
        config_path = Path("config.yaml")
        
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        try:
            return AppConfig.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"Invalid config.yaml:\n{exc}") from exc
    
    return AppConfig()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, settings.log_format)

    # 1. Load config.yaml
    app_config = _load_app_config()
    app.state.config = app_config
    logger.info("config loaded", provider=app_config.embedding.provider,
                vector_store=app_config.vector_store.backend)

    # 2. SQLite — run Alembic migrations (pragmas set via engine event)
    await init_db()
    logger.info("database migrations complete")

    # 3. Resolve and warm up embedding adapter
    try:
        from api.adapters.embeddings import get_embedding_adapter as resolve_embedding
        embedding_adapter = resolve_embedding(app_config.embedding)
        _ = embedding_adapter.dimensions  # triggers model load / warm-up
        set_embedding_adapter(embedding_adapter)
        logger.info("embedding adapter ready", provider=app_config.embedding.provider,
                    model=app_config.embedding.model, dimensions=embedding_adapter.dimensions)
    except Exception as exc:
        logger.error("embedding adapter unavailable", error=str(exc))

    # 4. Resolve vector store adapter
    try:
        from api.adapters.vector_store import get_vector_store as resolve_store
        from api.dependencies import get_embedding_adapter as _get_emb
        _emb = _get_emb()
        dims = _emb.dimensions if _emb else 384
        vector_store = resolve_store(app_config.vector_store, dims)
        set_vector_store(vector_store)
        logger.info("vector store ready", backend=app_config.vector_store.backend)
    except Exception as exc:
        logger.error("vector store unavailable", error=str(exc))

    # 5. Initialise Redis client (sync; used by BM25 search path)
    try:
        import redis as redis_lib

        r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
        set_redis_client(r)
        logger.info("redis client ready", url=settings.redis_url)
    except Exception as exc:
        logger.error("redis client unavailable", error=str(exc))

    logger.info("EmbedBase API ready")
    yield

    logger.info("EmbedBase API shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="EmbedBase",
        description="Local-first document embedding system with REST and MCP APIs",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)

    app.include_router(health.router)
    app.include_router(workspaces.router)
    app.include_router(collections.router)
    app.include_router(documents.router)
    app.include_router(search.router)
    app.include_router(config.router)
    app.include_router(mcp.router)

    return app


app = create_app()
