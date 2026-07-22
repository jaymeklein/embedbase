"""Generic realtime WebSocket bridge: stream a Redis pub/sub topic to the browser.

``GET /ws?topic=<topic>&key=<api-key>`` authenticates the key (browsers can't set
headers on a WebSocket, so it rides as a query param), then forwards every event
published to that topic (see :mod:`api.services.realtime`) to the socket — replaying
the per-topic snapshot first so a freshly connected or refreshed client sees the
current state immediately.

Reusable for any topic. Authorization reuses the HTTP auth: a topic shaped
``<name>:<collection_id>`` (e.g. ``ingestion:col_123``) is checked against that
collection via :func:`resolve_bearer` (a console session JWT or an API key). The
global ``ingestion-queue`` topic (every collection's progress) is grant-scoped —
master/admin get the full stream, a non-admin gets only events for collections
their grants can read (filtered per event below). Any other topic requires
master-equivalent access. The first consumer is ingestion progress
(topic ``ingestion:{collection_id}``).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.services import permissions, realtime
from api.services.auth import resolve_bearer
from api.settings import settings

logger = structlog.get_logger()

router = APIRouter(tags=["realtime"])

# Application-defined WebSocket close codes (4000–4999 range).
_WS_UNAUTHORIZED = 4401
_WS_FORBIDDEN = 4403


@router.websocket("/ws")
async def realtime_ws(
    websocket: WebSocket,
    topic: str,
    key: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    await websocket.accept()

    # A collection-scoped topic is authorized (read) against that collection. The global
    # ingestion-queue topic is grant-scoped: master/admin see all, a non-admin only their
    # readable collections (``allowed`` — None means unfiltered). Any other topic is master-only.
    collection_id = topic.split(":", 1)[1] if topic.startswith("ingestion:") else None
    allowed: set[str] | None = None
    try:
        principal = await resolve_bearer(key, db)
        if collection_id is not None:
            await permissions.authorize_collection(db, principal, collection_id, "read")
        elif topic == "ingestion-queue":
            scope = await permissions.readable_collection_scope(db, principal)
            allowed = None if scope is None else set(scope)
        elif not principal.is_master:
            raise HTTPException(403, "Master API key required")
    except HTTPException as exc:
        code = _WS_UNAUTHORIZED if exc.status_code == 401 else _WS_FORBIDDEN
        await websocket.close(code=code)
        return

    channel = realtime.channel(topic)
    # Typed Any: the installed redis (6.x) has aclose(), but the types-redis stub CI
    # pins still only knows close() — annotate to skip that stale-stub attr check.
    client: Any = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        # Replay latest state per key so a fresh / refreshed client isn't blank.
        for raw in (await client.hgetall(channel)).values():
            if _visible(raw, allowed):
                await websocket.send_text(raw)
        # Forward published events until the client goes away. Race the redis relay
        # against a socket read so a client disconnect — which pubsub.listen() can't
        # observe on its own — breaks us out and frees the connection.
        relay = asyncio.create_task(_forward(pubsub, websocket, allowed))
        watch = asyncio.create_task(_watch_disconnect(websocket))
        done, pending = await asyncio.wait({relay, watch}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await client.aclose()


async def _forward(pubsub: Any, websocket: WebSocket, allowed: set[str] | None) -> None:
    """Relay every published message on the subscribed channel to the socket, dropping any the
    caller isn't scoped to see (``allowed`` — see :func:`_visible`)."""
    async for message in pubsub.listen():
        if message.get("type") == "message" and _visible(message["data"], allowed):
            await websocket.send_text(message["data"])


def _visible(raw: str, allowed: set[str] | None) -> bool:
    """Whether an event may be sent to this client. ``allowed=None`` = unrestricted
    (master/admin, or a collection-scoped topic already authorized as a whole). Otherwise keep
    only events whose ``collection_id`` is in the readable set; a malformed or collection-less
    event is withheld from a scoped viewer — fail closed, never leak another tenant's activity.
    """
    if allowed is None:
        return True
    try:
        data = json.loads(raw)
        return isinstance(data, dict) and data.get("collection_id") in allowed
    except (ValueError, TypeError):
        return False


async def _watch_disconnect(websocket: WebSocket) -> None:
    """Resolve when the client disconnects; inbound frames are ignored."""
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
