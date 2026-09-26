"""Extended tests for backend/api/websocket.py — WebSocket endpoint coverage."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.api import websocket as ws_module
from backend.api.websocket import emit, emit_sync, log_bus, router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# emit (async) — additional coverage
# ---------------------------------------------------------------------------

class TestEmitAsync:
    @pytest.mark.asyncio
    async def test_puts_multiple_messages(self):
        # drain queue first
        while not log_bus.empty():
            log_bus.get_nowait()

        await emit("first")
        await emit("second")

        m1 = await asyncio.wait_for(log_bus.get(), timeout=1.0)
        m2 = await asyncio.wait_for(log_bus.get(), timeout=1.0)
        assert m1 == "first"
        assert m2 == "second"


# ---------------------------------------------------------------------------
# emit_sync — running loop branch
# ---------------------------------------------------------------------------

class TestEmitSyncRunningLoop:
    @pytest.mark.asyncio
    async def test_emit_sync_schedules_future_in_running_loop(self):
        """emit_sync called from within an async context (running loop) should enqueue via ensure_future."""
        before = log_bus.qsize()
        emit_sync("from async context")
        # Give the scheduled coroutine a chance to run
        await asyncio.sleep(0.05)
        after = log_bus.qsize()
        assert after > before


# ---------------------------------------------------------------------------
# websocket_logs endpoint
# ---------------------------------------------------------------------------

class TestWebsocketLogsEndpoint:
    def test_websocket_connects_and_disconnects_cleanly(self):
        """
        The websocket_logs endpoint accepts a connection, adds it to _clients,
        and removes it again on disconnect.  This exercises the full accept/
        finally cleanup path (lines 42-71).
        """
        app = _make_app()

        from backend.api.websocket import _clients
        before = len(_clients)

        with TestClient(app) as client:
            with client.websocket_connect("/ws/logs"):
                # Connection is live — client is registered
                during = len(_clients)
                assert during == before + 1

        # After disconnect the entry is cleaned up
        after = len(_clients)
        assert after == before

    def test_multiple_clients_each_removed_on_disconnect(self):
        """
        Two simultaneous WebSocket connections both get registered and both get
        cleaned up after they disconnect.
        """
        app = _make_app()
        from backend.api.websocket import _clients

        before = len(_clients)

        with TestClient(app) as client1:
            with client1.websocket_connect("/ws/logs"):
                with TestClient(app) as client2:
                    with client2.websocket_connect("/ws/logs"):
                        assert len(_clients) == before + 2
                # client2 disconnected
                assert len(_clients) == before + 1
            # client1 disconnected
        assert len(_clients) == before
