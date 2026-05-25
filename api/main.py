from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.db import get_connection, run_migrations
from api.dependencies import set_embedding_adapter, set_vector_store
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
        return AppConfig.model_validate(data)
    return AppConfig()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, settings.log_format)

    # 1. Load config.yaml
    app_config = _load_app_config()
    app.state.config = app_config
    logger.info("config loaded", provider=app_config.embedding.provider,
                vector_store=app_config.vector_store.backend)

    # 2. SQLite — WAL mode, foreign keys, run migrations
    db = await get_connection()
    await run_migrations(db)
    await db.close()
    logger.info("database migrations complete")

    # 3. Resolve and warm up embedding adapter
    from api.adapters.embeddings import get_embedding_adapter as resolve_embedding
    embedding_adapter = resolve_embedding(app_config.embedding)
    _ = embedding_adapter.dimensions  # triggers model load / warm-up
    set_embedding_adapter(embedding_adapter)
    logger.info("embedding adapter ready", provider=app_config.embedding.provider,
                model=app_config.embedding.model, dimensions=embedding_adapter.dimensions)

    # 4. Resolve vector store adapter
    from api.adapters.vector_store import get_vector_store as resolve_store
    vector_store = resolve_store(app_config.vector_store, embedding_adapter.dimensions)
    set_vector_store(vector_store)
    logger.info("vector store ready", backend=app_config.vector_store.backend)

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
