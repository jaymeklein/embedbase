"""Build the EmbedBase ``FastMCP`` server and its streamable-HTTP ASGI app.

The tool wrappers here resolve runtime dependencies (DB session + adapter
singletons) and delegate to :mod:`api.services.mcp.tools`, which holds the
testable logic. The streamable-HTTP app is mounted at ``/mcp`` and — because the
API carries ``root_path="/api"`` — is reached externally at ``/api/mcp/``. It is
wrapped with the auth + rate-limit middleware, and its session manager is run for
the app's lifetime by the lifespan (see :mod:`api.main`).

The transport is stateless streamable HTTP (``stateless_http`` + ``json_response``):
each tool call is a self-contained request/response with no long-lived stream to
drop across an ``uvicorn --reload`` — the failure mode of the previous SSE
transport. API surface verified against ``mcp==1.28.1`` via runtime introspection.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any, Literal

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from starlette.types import ASGIApp

from api.db import AsyncSessionLocal
from api.dependencies import (
    get_embedding_adapter,
    get_mcp_config,
    get_redis_client,
    get_reranker,
    get_search_config,
    get_vector_store,
)
from api.models.config import MCPConfig
from api.services import jobs as jobs_svc
from api.services.mcp import tools
from api.services.mcp.context import current_principal, current_rate_limit
from api.services.mcp.middleware import build_mcp_middleware


def _require[T](value: T | None, name: str) -> T:
    """Return ``value`` or raise if the backing adapter is not yet ready."""
    if value is None:
        raise RuntimeError(f"{name} backend not ready")
    return value


def _register_tools(server: FastMCP, *, max_results: int) -> None:
    """Register the core read / search tools (workspace listing, search, document listing)."""

    @server.tool()
    async def list_workspaces() -> dict[str, Any]:
        """List all workspaces, each with its collections and document counts.

        Start here: the returned workspace and collection ``id`` values are what the other tools
        (search, upload, document, tag, and CRUD) take as arguments — never invent an id. Filtered
        to what your key may read.
        """
        async with AsyncSessionLocal() as db:
            return await tools.list_workspaces(db=db, principal=current_principal())

    @server.tool()
    async def search_documents(
        query: str,
        collection_ids: list[str],
        top_k: int = 5,
        hybrid: bool = True,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Hybrid semantic + keyword (BM25) search across one or more collections — the main
        retrieval tool.

        ``collection_ids`` are collection ``id``s from ``list_workspaces`` (at least one; only the
        ones you may read are searched). ``top_k`` is clamped to the server's ``mcp.max_results``.
        ``hybrid=False`` runs semantic-only. ``filters`` is an optional metadata filter shaped
        ``{"filename": "<substring>", "tags": ["<tag name>", ...]}`` — both keys optional, and a hit
        must carry every listed tag.

        Each result carries a ``chunk_id`` (pass it to ``get_document_chunks``), ``text``,
        ``score``, and a ``source`` (document / collection / workspace + page). When relevant
        chunks fell below the ``top_k`` cut the response sets ``more_available`` and adds a
        ``notice`` — if it is present and the answer looks incomplete, re-run with a higher
        ``top_k`` before responding.
        """
        embedder = _require(get_embedding_adapter(), "Embedding")
        vector_store = _require(get_vector_store(), "Vector store")
        search_config = get_search_config()
        async with AsyncSessionLocal() as db:
            return await tools.search_documents(
                query=query,
                collection_ids=collection_ids,
                top_k=top_k,
                hybrid=hybrid,
                filters=filters,
                max_results=max_results,
                expand_neighbors=search_config.effective_expand_neighbors,
                expand_char_budget=search_config.expand_char_budget,
                db=db,
                principal=current_principal(),
                embedder=embedder,
                vector_store=vector_store,
                reranker=get_reranker(),  # optional — None skips the rerank stage
            )

    @server.tool()
    async def list_documents(collection_id: str) -> dict[str, Any]:
        """List the documents in a collection with their ingestion status."""
        async with AsyncSessionLocal() as db:
            return await tools.list_documents(
                collection_id=collection_id, db=db, principal=current_principal()
            )


def _register_document_lifecycle_tools(server: FastMCP) -> None:
    """Register the document tools: presigned upload, container-path ingest, status, reprocess,
    download, chunk inspection, and delete."""

    @server.tool()
    async def ingest_document(
        collection_id: str, file_path: str, retention_days: int | None = None
    ) -> dict[str, Any]:
        """Ingest a container-local file (by path) into a collection (master key only).

        Set ``retention_days`` (1-30) to auto-purge the document after that many days;
        omit it for a permanent document.
        """
        async with AsyncSessionLocal() as db:
            return await tools.ingest_document(
                collection_id=collection_id, file_path=file_path,
                retention_days=retention_days, db=db, principal=current_principal(),
            )

    @server.tool()
    async def request_upload(
        collection_id: str, filename: str, retention_days: int | None = None
    ) -> dict[str, Any]:
        """Reserve a document and get a presigned PUT URL to upload a file (step 1 of 2).

        ``PUT`` the raw file bytes to the returned ``upload_url``, then call ``confirm_upload``
        with the ``document_id`` to verify the upload and start ingestion. Set ``retention_days``
        (1-30) to auto-purge the document after that many days; omit for a permanent document.
        Requires ``write`` on the collection and an S3/MinIO storage backend.
        """
        async with AsyncSessionLocal() as db:
            return await tools.request_upload(
                collection_id=collection_id, filename=filename,
                retention_days=retention_days, db=db, principal=current_principal(),
            )

    @server.tool()
    async def confirm_upload(document_id: str) -> dict[str, Any]:
        """Finalize a presigned upload once the ``PUT`` succeeds (step 2 of 2): verify + ingest."""
        async with AsyncSessionLocal() as db:
            return await tools.confirm_upload(
                document_id=document_id, db=db, principal=current_principal()
            )

    @server.tool()
    async def request_original_upload(document_id: str, filename: str) -> dict[str, Any]:
        """Attach an original source file to a document — get a presigned PUT URL (step 1 of 2).

        Optional: keep the raw source (e.g. the PDF a Markdown upload was converted from) alongside
        the parse. It is stored under the same document and is never embedded. ``PUT`` the bytes to
        the returned ``upload_url``, then call ``confirm_original_upload``. Fetch it later with
        ``download_document(document_id, original=true)``. Requires ``write`` + an S3/MinIO backend.
        """
        async with AsyncSessionLocal() as db:
            return await tools.request_original_upload(
                document_id=document_id, filename=filename, db=db, principal=current_principal()
            )

    @server.tool()
    async def confirm_original_upload(document_id: str) -> dict[str, Any]:
        """Finalize an original-source-file attach once the ``PUT`` succeeds (step 2 of 2)."""
        async with AsyncSessionLocal() as db:
            return await tools.confirm_original_upload(
                document_id=document_id, db=db, principal=current_principal()
            )

    @server.tool()
    async def reprocess_document(document_id: str) -> dict[str, Any]:
        """Re-enqueue a document's ingestion — the manual retry for a failed file (reuses bytes)."""
        async with AsyncSessionLocal() as db:
            return await tools.reprocess_document(
                document_id=document_id, db=db, principal=current_principal()
            )

    @server.tool()
    async def get_document_status(document_id: str) -> dict[str, Any]:
        """Return the latest ingestion status for a document.

        ``status`` is one of: ``awaiting_upload`` (reserved via request_upload, bytes not yet
        confirmed), ``pending`` / ``processing`` (ingesting), ``done``, ``failed`` (retry with
        ``reprocess_document``), ``rate_limited`` (paused on the embedding provider's quota; resumes
        on its own), or ``deleting``.
        """
        async with AsyncSessionLocal() as db:
            return await tools.get_document_status(
                document_id=document_id, db=db, principal=current_principal()
            )

    @server.tool()
    async def download_document(document_id: str, original: bool = False) -> dict[str, Any]:
        """Get a short-lived presigned URL to download a document's bytes.

        Returns ``{document_id, filename, url, url_expires_in}``. On local-disk storage (which
        can't presign) ``url`` is ``null`` and a ``notice`` points at the REST byte endpoint. Set
        ``original=true`` to download the attached original source file instead of the parse.
        """
        async with AsyncSessionLocal() as db:
            return await tools.download_document(
                document_id=document_id, original=original, db=db, principal=current_principal()
            )

    @server.tool()
    async def get_checksum(document_id: str, original: bool = False) -> dict[str, Any]:
        """Compute a fresh checksum of a document's stored bytes — prove it's intact and unchanged.

        Hashes the stored file from scratch on every call (nothing cached), so the digest always
        reflects the bytes exactly as stored right now. Compare it to the hash of your local copy
        (e.g. ``sha256sum <file>``) to confirm the upload was stored intact and hasn't drifted.
        Returns ``{document_id, filename, original, algorithm, checksum, file_size, ingested_at}`` —
        ``ingested_at`` also tells you whether the stored copy predates a newer local file. Set
        ``original=true`` to checksum the attached original source file instead of the parse.
        """
        async with AsyncSessionLocal() as db:
            return await tools.get_checksum(
                document_id=document_id, original=original, db=db, principal=current_principal()
            )

    @server.tool()
    async def get_document_chunks(
        document_id: str, chunk_ids: list[str] | None = None,
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        """Inspect a document's stored chunks (text + metadata).

        Pass ``chunk_ids`` (e.g. the ``chunk_id``s returned by ``search_documents``) to fetch just
        those chunks — at most 100, ids from another document ignored. Omit ``chunk_ids`` to page
        through the whole document in chunk order via ``limit`` (1-100, default 50) + ``offset``;
        the result reports ``total`` and ``has_more`` so you can page to the end.
        """
        vector_store = _require(get_vector_store(), "Vector store")
        async with AsyncSessionLocal() as db:
            return await tools.get_document_chunks(
                document_id=document_id, chunk_ids=chunk_ids, limit=limit, offset=offset,
                db=db, principal=current_principal(), vector_store=vector_store,
            )

    @server.tool()
    async def delete_document(document_id: str) -> dict[str, Any]:
        """Delete a document and enqueue async vector + BM25 cleanup."""
        async with AsyncSessionLocal() as db:
            return await tools.delete_document(
                document_id=document_id, db=db, principal=current_principal()
            )


def _register_structure_tools(server: FastMCP) -> None:
    """Register workspace + collection CRUD tools (writes are grant-scoped like the REST routes)."""

    @server.tool()
    async def create_workspace(
        name: str, description: str = "", color: str = "#6366f1", icon: str = "folder"
    ) -> dict[str, Any]:
        """Create a top-level workspace (requires the create_workspace capability)."""
        async with AsyncSessionLocal() as db:
            return await tools.create_workspace(
                name=name, description=description, color=color, icon=icon,
                db=db, principal=current_principal(),
            )

    @server.tool()
    async def update_workspace(
        workspace_id: str, name: str | None = None, description: str | None = None,
        color: str | None = None, icon: str | None = None,
    ) -> dict[str, Any]:
        """Edit a workspace's metadata (only the fields you pass). Requires write on it."""
        async with AsyncSessionLocal() as db:
            return await tools.update_workspace(
                workspace_id=workspace_id, name=name, description=description,
                color=color, icon=icon, db=db, principal=current_principal(),
            )

    @server.tool()
    async def delete_workspace(workspace_id: str) -> dict[str, Any]:
        """Delete a workspace, cascading to its collections + documents. Requires write on it."""
        async with AsyncSessionLocal() as db:
            return await tools.delete_workspace(
                workspace_id=workspace_id, db=db, principal=current_principal()
            )

    @server.tool()
    async def create_collection(
        workspace_id: str, name: str, description: str = "",
        color: str = "#8b5cf6", icon: str = "book",
    ) -> dict[str, Any]:
        """Create a collection inside a workspace. Requires write on the workspace."""
        async with AsyncSessionLocal() as db:
            return await tools.create_collection(
                workspace_id=workspace_id, name=name, description=description,
                color=color, icon=icon, db=db, principal=current_principal(),
            )

    @server.tool()
    async def update_collection(
        workspace_id: str, collection_id: str, name: str | None = None,
        description: str | None = None, color: str | None = None, icon: str | None = None,
    ) -> dict[str, Any]:
        """Edit a collection's metadata (only the fields you pass). Requires write on it or an ancestor."""
        async with AsyncSessionLocal() as db:
            return await tools.update_collection(
                workspace_id=workspace_id, collection_id=collection_id, name=name,
                description=description, color=color, icon=icon,
                db=db, principal=current_principal(),
            )

    @server.tool()
    async def delete_collection(workspace_id: str, collection_id: str) -> dict[str, Any]:
        """Delete a collection, cascading to its documents. Requires write on it or an ancestor."""
        async with AsyncSessionLocal() as db:
            return await tools.delete_collection(
                workspace_id=workspace_id, collection_id=collection_id,
                db=db, principal=current_principal(),
            )


def _register_tag_tools(server: FastMCP) -> None:
    """Register tag CRUD + assignment tools (gated on the ``manage_tags`` capability)."""

    @server.tool()
    async def list_tags(workspace_id: str) -> dict[str, Any]:
        """List a workspace's tags with usage counts (requires the manage_tags capability)."""
        async with AsyncSessionLocal() as db:
            return await tools.list_tags(
                workspace_id=workspace_id, db=db, principal=current_principal()
            )

    @server.tool()
    async def create_tag(
        workspace_id: str, name: str, color: str | None = None
    ) -> dict[str, Any]:
        """Create a tag in a workspace (requires the manage_tags capability)."""
        async with AsyncSessionLocal() as db:
            return await tools.create_tag(
                workspace_id=workspace_id, name=name, color=color,
                db=db, principal=current_principal(),
            )

    @server.tool()
    async def update_tag(
        workspace_id: str, tag_id: str, name: str | None = None, color: str | None = None
    ) -> dict[str, Any]:
        """Rename/recolor a tag (only the fields you pass; requires the manage_tags capability)."""
        async with AsyncSessionLocal() as db:
            return await tools.update_tag(
                workspace_id=workspace_id, tag_id=tag_id, name=name, color=color,
                db=db, principal=current_principal(),
            )

    @server.tool()
    async def delete_tag(workspace_id: str, tag_id: str) -> dict[str, Any]:
        """Delete a tag, removing its assignments (requires the manage_tags capability)."""
        async with AsyncSessionLocal() as db:
            return await tools.delete_tag(
                workspace_id=workspace_id, tag_id=tag_id, db=db, principal=current_principal()
            )

    @server.tool()
    async def merge_tags(workspace_id: str, source_id: str, target_id: str) -> dict[str, Any]:
        """Merge one tag's assignments into another, then delete the source (manage_tags capability)."""
        async with AsyncSessionLocal() as db:
            return await tools.merge_tags(
                workspace_id=workspace_id, source_id=source_id, target_id=target_id,
                db=db, principal=current_principal(),
            )

    @server.tool()
    async def assign_tag(
        workspace_id: str, tag_id: str,
        target: Literal["workspace", "collection", "document"] = "workspace",
        collection_id: str | None = None, document_id: str | None = None,
    ) -> dict[str, Any]:
        """Attach a tag to a workspace, collection, or document (pick with ``target``).

        ``collection_id`` is required when ``target`` is ``collection`` or ``document``;
        ``document_id`` is also required when ``target`` is ``document``. Requires the manage_tags
        capability (plus write on the collection/document for those targets).
        """
        async with AsyncSessionLocal() as db:
            return await tools.assign_tag(
                workspace_id=workspace_id, tag_id=tag_id, target=target,
                collection_id=collection_id, document_id=document_id,
                db=db, principal=current_principal(),
            )

    @server.tool()
    async def unassign_tag(
        workspace_id: str, tag_id: str,
        target: Literal["workspace", "collection", "document"] = "workspace",
        collection_id: str | None = None, document_id: str | None = None,
    ) -> dict[str, Any]:
        """Detach a tag from a workspace, collection, or document (same args as assign_tag)."""
        async with AsyncSessionLocal() as db:
            return await tools.unassign_tag(
                workspace_id=workspace_id, tag_id=tag_id, target=target,
                collection_id=collection_id, document_id=document_id,
                db=db, principal=current_principal(),
            )


async def _embedding_pause_seconds() -> int:
    """Resolve the global embedding-quota backoff, offloading the sync redis TTL read to a thread
    (repo convention, cf. api/routers/jobs.py) so a redis stall can't block the event loop."""
    return await asyncio.to_thread(jobs_svc.embedding_pause_seconds, get_redis_client())


def _register_ops_tools(server: FastMCP) -> None:
    """Register ingestion-queue + rate-limit introspection tools."""

    @server.tool()
    async def list_ingestion_jobs(
        limit: int = 50, offset: int = 0, status: str | None = None,
        collection: str | None = None, filename: str | None = None,
    ) -> dict[str, Any]:
        """List ingestion jobs (active first, then newest-first), scoped to your readable collections.

        Optional AND-combined filters: ``status`` (exact — one of pending / processing / done /
        failed / rate_limited), ``collection`` (name substring), ``filename`` (substring). ``limit``
        is 1-200 (default 50). Returns ``{items, total, limit, offset}``.
        """
        async with AsyncSessionLocal() as db:
            return await tools.list_ingestion_jobs(
                limit=limit, offset=offset, status=status, collection=collection,
                filename=filename, db=db, principal=current_principal(),
            )

    @server.tool()
    async def get_ingestion_stats() -> dict[str, Any]:
        """Per-status ingestion-job counts (scoped to your collections) + the embedding-quota pause."""
        pause = await _embedding_pause_seconds()
        async with AsyncSessionLocal() as db:
            return await tools.get_ingestion_stats(
                embedding_pause_seconds=pause, db=db, principal=current_principal()
            )

    @server.tool()
    async def get_rate_limit() -> dict[str, Any]:
        """Your current MCP call budget (limit/remaining/reset) + any global ingestion pause."""
        pause = await _embedding_pause_seconds()
        return await tools.get_rate_limit(
            rate_limit=current_rate_limit(), embedding_pause_seconds=pause
        )


# ── Tool catalogue (drives the Settings → MCP page + its generated SKILL.md) ───
#
# The domain tool groups in display order: (label, registrar). build_mcp_server (the live server)
# and build_tool_catalog (the introspected catalogue) both drive off this one list, so adding a
# group — or a tool to a group's ``_register_*`` helper — surfaces in both with no hand-kept mirror.
# The core read/search group is separate only because its registrar needs the runtime max_results.
_DOMAIN_REGISTRARS: list[tuple[str, Callable[[FastMCP], None]]] = [
    ("Documents", _register_document_lifecycle_tools),
    ("Workspaces & collections", _register_structure_tools),
    ("Tags", _register_tag_tools),
    ("Ops", _register_ops_tools),
]
_CORE_GROUP_LABEL = "Search & read"


def _tool_summary(description: str | None) -> str:
    """The first paragraph of a tool's docstring, whitespace-collapsed to a single line.

    Collapses RST ``literal`` runs to a single markdown backtick so the summary reads cleanly in
    both the plain-text tool list and the markdown SKILL.md.
    """
    if not description:
        return ""
    first_para = description.strip().split("\n\n", 1)[0]
    collapsed = re.sub(r"\s+", " ", first_para).strip()
    return re.sub(r"`+", "`", collapsed)


def _format_default(value: Any) -> str:
    """Render a JSON-schema default the way it reads in a call signature (bools lower-cased)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _tool_signature(input_schema: dict[str, Any]) -> str:
    """Synthesize a Python-like call signature from a tool's JSON input schema.

    Required params render bare; optional ones as ``name=default`` when they carry a non-null
    default, else ``name?``. Property order follows the schema (the function's parameter order).
    """
    props: dict[str, Any] = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    parts: list[str] = []
    for name, spec in props.items():
        default = spec.get("default") if isinstance(spec, dict) else None
        if name in required:
            parts.append(name)
        elif default not in (None, ""):  # skip empty-string defaults — "name=" reads as noise
            parts.append(f"{name}={_format_default(default)}")
        else:
            parts.append(f"{name}?")
    return "(" + ", ".join(parts) + ")"


async def build_tool_catalog(*, max_results: int = 20) -> list[dict[str, Any]]:
    """The registered MCP tools as a grouped catalogue — ``[{group, tools: [{name, signature, summary}]}]``.

    Each group is built by probing a throwaway ``FastMCP`` with that group's ``_register_*`` helper,
    so the grouping and every tool's name / parameters / description come straight from the same code
    that builds the live server. The Settings → MCP page and its generated SKILL.md render from this,
    so they track the real tool surface automatically — add or change a tool and both update with no
    hand-kept list. ``max_results`` affects only search behaviour, never a tool's schema.
    """
    groups: list[tuple[str, Callable[[FastMCP], None]]] = [
        (_CORE_GROUP_LABEL, lambda s: _register_tools(s, max_results=max_results)),
        *_DOMAIN_REGISTRARS,
    ]
    catalog: list[dict[str, Any]] = []
    for label, registrar in groups:
        probe = FastMCP("embedbase-catalog")
        registrar(probe)
        entries = await probe.list_tools()
        catalog.append(
            {
                "group": label,
                "tools": [
                    {
                        "name": t.name,
                        "signature": _tool_signature(t.inputSchema or {}),
                        "summary": _tool_summary(t.description),
                    }
                    for t in entries
                ],
            }
        )
    return catalog


def build_mcp_server(*, max_results: int = 20) -> FastMCP:
    """Construct the ``FastMCP`` server with all EmbedBase tools registered.

    Uses the **streamable-HTTP** transport in ``stateless_http`` + ``json_response``
    mode: each tool call is a self-contained HTTP request/response, with no
    long-lived SSE stream and no server-side session to expire. That survives
    ``uvicorn --reload`` and Docker Desktop's port-forwarding, unlike the deprecated
    SSE transport (whose single persistent stream silently died on any reload and
    could not be re-established without a full client reconnect).

    Args:
        max_results: Upper bound applied to ``search_documents`` ``top_k``.

    Returns:
        A ready-to-serve :class:`FastMCP` instance.
    """
    server = FastMCP("embedbase", stateless_http=True, json_response=True)
    # NB: per-request auth relies on ``stateless_http=True`` — each request spawns its
    # own handler task, so the middleware's ContextVar principal (context.py) is bound
    # per request. Under stateful sessions the handler task is per-session, which would
    # pin the first caller's principal to every later call. Do not flip this flag.
    # Serve at the mount root so the external endpoint is a clean ``/api/mcp``
    # (this app is mounted at ``/mcp`` and the app carries root_path ``/api``).
    server.settings.streamable_http_path = "/"
    _register_tools(server, max_results=max_results)  # core: needs the runtime max_results
    for _label, registrar in _DOMAIN_REGISTRARS:
        registrar(server)
    return server


def build_mcp_asgi_app(config: MCPConfig) -> tuple[ASGIApp, FastMCP]:
    """Build the streamable-HTTP ASGI app for ``/mcp``, guarded by auth + limits.

    The rate limit is wired as a *live read* of the ``mcp`` config rather than the
    ``config`` snapshot passed here: this app is built once at startup and never
    rebuilt, so capturing the value would freeze it until the next restart. Reading
    it per request means a config save applies to the very next MCP call.

    Args:
        config: The resolved ``mcp`` config section, for the startup-time result cap.

    Returns:
        ``(asgi_app, server)`` — the auth/rate-limit-wrapped streamable-HTTP app,
        and the :class:`FastMCP` server whose session manager the caller must run
        for the app's lifetime (see :func:`mount_app` + the app lifespan).
    """
    server = build_mcp_server(max_results=config.max_results)
    http_app = server.streamable_http_app()
    guarded = build_mcp_middleware(
        http_app, rate_limit_rpm=lambda: get_mcp_config().rate_limit_rpm
    )
    return guarded, server


def mount_app(app: FastAPI, config: MCPConfig) -> None:
    """Mount the MCP streamable-HTTP app at ``/mcp`` (→ ``/api/mcp``) when enabled.

    Owns the enablement decision so the router stays a pure delegation. Stashes the
    server on ``app.state.mcp_server`` so the app lifespan can run its streamable
    session manager — required before the endpoint can serve requests.

    Args:
        app: The FastAPI application to mount onto.
        config: The resolved ``mcp`` config; mounting is skipped when disabled.
    """
    if not config.enabled:
        return
    asgi_app, server = build_mcp_asgi_app(config)
    app.mount("/mcp", asgi_app)
    app.state.mcp_server = server
