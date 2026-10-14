"""In-memory log bus and WebSocket log-streaming endpoint."""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# Module-level asyncio Queue — agent steps put log lines here.
# The WebSocket handler reads and broadcasts to all connected clients.
log_bus: asyncio.Queue = asyncio.Queue()

# Connected WebSocket clients
_clients: list[WebSocket] = []

# Reference to the main event loop, captured at startup by init_loop().
# Worker threads use this to schedule onto the correct loop via
# call_soon_threadsafe(), which is the only safe cross-thread mechanism.
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def init_loop() -> None:
    """
    Capture the running event loop for later use by worker threads.
    Must be called from an async context at application startup
    (i.e. inside the FastAPI lifespan handler).
    """
    global _main_loop
    _main_loop = asyncio.get_running_loop()


async def emit(line: str) -> None:
    """Put a log line on the bus (called by agent steps)."""
    await log_bus.put(line)


def emit_sync(line: str) -> None:
    """
    Thread-safe log emission for synchronous agent code running in a worker
    thread (e.g. via asyncio.to_thread).

    asyncio.get_event_loop() is unreliable in threads under Python 3.10+: in a
    worker thread it creates a *new* non-running loop instead of returning the
    main event loop, so loop.is_running() is False and puts land on an orphan
    loop that nothing drains — silently dropping every log message.

    The correct pattern is call_soon_threadsafe() against the loop captured
    at startup, which safely hands the coroutine back to the event-loop thread.
    """
    if _main_loop is None or not _main_loop.is_running():
        return
    _main_loop.call_soon_threadsafe(_main_loop.create_task, emit(line))


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
