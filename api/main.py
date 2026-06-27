import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from api.db import init_db
from api.dependencies import (
    set_app_config,
    set_embedding_adapter,
    set_redis_client,
    set_vector_store,
)
from api.middleware import RequestIDMiddleware, configure_logging
from api.models.config import AppConfig
from api.routers import (
    collections,
    config,
    documents,
    graph,
    health,
    indexing,
    mcp,
    search,
    tags,
    workspaces,
)
from api.services.config_env import overlay_parser_env, overlay_vector_store_env
from api.settings import settings

logger = structlog.get_logger()


# REST endpoints the MCP/AI integration uses; the standalone reference exposes only
# these, not the app's internal surface (config, health, graph, the MCP mount).
_REFERENCE_PREFIXES = ("/search", "/workspaces", "/documents")


def _is_reference_route(path: str) -> bool:
    """True for the integration endpoints exposed in the standalone reference."""
    return path.startswith(_REFERENCE_PREFIXES)


def _register_reference(app: FastAPI) -> None:
    """Serve a standalone Swagger reference of just the integration endpoints.

    Distinct from ``/docs`` (the full app surface): an MCP consumer gets a focused
    OpenAPI of the search + workspace/collection/document endpoints only.

    Filtering happens on the generated spec's ``paths`` rather than on
    ``app.routes``: since Starlette 1.3 ``include_router`` nests routers as
    sub-mounts, so the top-level routes no longer carry the leaf path. Letting
    ``get_openapi`` flatten everything first, then keeping the integration paths,
    is independent of how routers are stored.
    """

    @app.get("/reference.json", include_in_schema=False)
    def reference_spec() -> JSONResponse:
        spec = get_openapi(
            title="EmbedBase REST API reference",
            version="1.0.0",
            description="Integration endpoints for MCP/AI consumers.",
            routes=app.routes,
            servers=[{"url": "/api"}],
        )
        spec["paths"] = {
            path: item
            for path, item in spec.get("paths", {}).items()
            if _is_reference_route(path)
        }
        return JSONResponse(spec)

    @app.get("/reference", include_in_schema=False)
    def reference_docs(request: Request):
        # Prepend the proxy prefix (root_path) so the spec URL resolves through nginx.
        root = request.scope.get("root_path", "").rstrip("/")
        return get_swagger_ui_html(
            openapi_url=f"{root}/reference.json", title="EmbedBase API reference"
        )


def _load_app_config() -> AppConfig:
    config_path = Path("/app/config.yaml")
    if not config_path.exists():
        config_path = Path("config.yaml")

    data: dict = {}
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

    # Env vars (e.g. from docker-compose.postgres.yml or .env) override the file so
    # the vector-store backend + secrets and the docling models path can be selected
    # without editing config.yaml.
    data = overlay_parser_env(overlay_vector_store_env(data))
    try:
        return AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"Invalid config.yaml:\n{exc}") from exc


async def _warm_up_adapters(app_config: AppConfig) -> None:
    """Load the embedding model + vector store off the startup path.

    Constructing the sentence-transformers adapter pulls in ``torch`` and loads
    the model, which takes tens of seconds. Doing it inline in :func:`lifespan`
    (before ``yield``) held the ASGI server's startup open, so uvicorn would not
    accept *any* request — not even ``/healthz`` — until the model was ready.
    Run as a background task instead: the dependency getters return ``None`` and
    ``/healthz`` reports ``embedding_model_loaded: false`` until each adapter is
    set, so the API is reachable immediately and warms up in parallel. The blocking
    loads run via :func:`asyncio.to_thread` so they never stall the event loop.
    """
    try:
        from api.adapters.embeddings import get_embedding_adapter as resolve_embedding
        embedding_adapter = await asyncio.to_thread(resolve_embedding, app_config.embedding)
        await asyncio.to_thread(lambda: embedding_adapter.dimensions)  # warm-up
        set_embedding_adapter(embedding_adapter)
        logger.info("embedding adapter ready", provider=app_config.embedding.provider,
                    model=app_config.embedding.model, dimensions=embedding_adapter.dimensions)
    except Exception as exc:
        logger.error("embedding adapter unavailable", error=str(exc))

    try:
        from api.adapters.vector_store import get_vector_store as resolve_store
        from api.dependencies import get_embedding_adapter as _get_emb
        _emb = _get_emb()
        dims = _emb.dimensions if _emb else 384
        vector_store = await asyncio.to_thread(resolve_store, app_config.vector_store, dims)
        set_vector_store(vector_store)
        logger.info("vector store ready", backend=app_config.vector_store.backend)
    except Exception as exc:
        logger.error("vector store unavailable", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.log_level, settings.log_format)

    # 1. Load config.yaml
    app_config = _load_app_config()
    app.state.config = app_config
    set_app_config(app_config)
    logger.info("config loaded", provider=app_config.embedding.provider,
                vector_store=app_config.vector_store.backend)

    # 2. SQLite — run Alembic migrations (pragmas set via engine event)
    await init_db()
    logger.info("database migrations complete")

    # 3. Warm up the embedding + vector-store adapters in the background so the
    #    slow model import doesn't block the server from serving (see helper).
    warm_up = asyncio.create_task(_warm_up_adapters(app_config))

    # 4. Initialise Redis client (sync; fast; used by BM25 search path)
    try:
        import redis as redis_lib

        r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
        set_redis_client(r)
        logger.info("redis client ready", url=settings.redis_url)
    except Exception as exc:
        logger.error("redis client unavailable", error=str(exc))

    logger.info("EmbedBase API ready")
    yield

    warm_up.cancel()
    logger.info("EmbedBase API shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="EmbedBase",
        description="Local-first document embedding system with REST and MCP APIs",
        version="1.0.0",
        lifespan=lifespan,
        # The console + MCP clients reach the API through the proxy's /api prefix
        # (nginx strips it; the app still serves at root). root_path tells FastAPI the
        # external prefix so Swagger UI (/api/docs), the spec (/api/openapi.json), and
        # "Try it out" all resolve correctly — the AI/MCP can read the REST standards.
        root_path="/api",
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
    app.include_router(tags.router)
    app.include_router(graph.router)
    app.include_router(search.router)
    app.include_router(indexing.router)
    app.include_router(config.router)

    # Standalone OpenAPI reference of just the integration endpoints (after routers
    # are registered so their routes exist to filter).
    _register_reference(app)

    # MCP server (Delivery 4) — a mounted SSE ASGI sub-app, not a normal router.
    # Mount last so its /mcp prefix never shadows the REST routes above.
    mcp.mount_mcp(app, _load_app_config().mcp)

    return app


app = create_app()
