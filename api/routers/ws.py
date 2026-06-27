"""Generic realtime WebSocket bridge: stream a Redis pub/sub topic to the browser.

``GET /ws?topic=<topic>&key=<api-key>`` authenticates the key (browsers can't set
headers on a WebSocket, so it rides as a query param), then forwards every event
published to that topic (see :mod:`api.services.realtime`) to the socket — replaying
the per-topic snapshot first so a freshly connected or refreshed client sees the
current state immediately.

Reusable for any topic. Authorization reuses the HTTP auth: a topic shaped
``<name>:<collection_id>`` (e.g. ``ingestion:col_123``) is checked against that
collection via :func:`authenticate_api_key`; any other topic requires the master
key. The first consumer is ingestion progress (topic ``ingestion:{collection_id}``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.services import realtime
from api.services.auth import authenticate_api_key
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

    # A collection-scoped topic is authorized against that collection; any other
    # topic requires the master key.
    collection_id = topic.split(":", 1)[1] if topic.startswith("ingestion:") else None
    try:
        principal = await authenticate_api_key(key, db, collection_id=collection_id)
    except HTTPException as exc:
        code = _WS_UNAUTHORIZED if exc.status_code == 401 else _WS_FORBIDDEN
        await websocket.close(code=code)
        return
    if collection_id is None and not principal.is_master:
        await websocket.close(code=_WS_FORBIDDEN)
        return

    channel = realtime.channel(topic)
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.subscribe(channel)
    try:
        # Replay latest state per key so a fresh / refreshed client isn't blank.
        for raw in (await client.hgetall(channel)).values():
            await websocket.send_text(raw)
        # Forward published events until the client goes away. Race the redis relay
        # against a socket read so a client disconnect — which pubsub.listen() can't
        # observe on its own — breaks us out and frees the connection.
        relay = asyncio.create_task(_forward(pubsub, websocket))
        watch = asyncio.create_task(_watch_disconnect(websocket))
        done, pending = await asyncio.wait({relay, watch}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await client.aclose()


async def _forward(pubsub: Any, websocket: WebSocket) -> None:
    """Relay every published message on the subscribed channel to the socket."""
    async for message in pubsub.listen():
        if message.get("type") == "message":
            await websocket.send_text(message["data"])


async def _watch_disconnect(websocket: WebSocket) -> None:
    """Resolve when the client disconnects; inbound frames are ignored."""
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        return
