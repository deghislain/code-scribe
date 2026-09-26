"""In-memory log bus and WebSocket log-streaming endpoint."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# Module-level asyncio Queue — agent steps put log lines here.
# The WebSocket handler reads and broadcasts to all connected clients.
log_bus: asyncio.Queue = asyncio.Queue()

# Connected WebSocket clients
_clients: list[WebSocket] = []


async def emit(line: str) -> None:
    """Put a log line on the bus (called by agent steps)."""
    await log_bus.put(line)


def emit_sync(line: str) -> None:
    """
    Thread-safe log emission for synchronous agent code.
    Creates a new event loop task if a running loop exists.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(emit(line))
        else:
            loop.run_until_complete(emit(line))
    except RuntimeError:
        pass


@router.websocket("/ws/logs")
async def websocket_logs(ws: WebSocket) -> None:
    await ws.accept()
    _clients.append(ws)
    try:
        # Start a background reader that drains the bus and broadcasts to all clients
        async def _broadcast() -> None:
            while True:
                line = await log_bus.get()
                dead: list[WebSocket] = []
                for client in list(_clients):
                    try:
                        await client.send_text(line)
                    except Exception:
                        dead.append(client)
                for d in dead:
                    if d in _clients:
                        _clients.remove(d)
                # Re-broadcast for remaining clients who joined after the item was consumed
                # NOTE: items are consumed once; later-joining clients see only future items.

        task = asyncio.create_task(_broadcast())
        # Keep the connection alive until client disconnects
        while True:
            try:
                await ws.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        if ws in _clients:
            _clients.remove(ws)
        task.cancel()
