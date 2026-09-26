"""Tests for backend/api/websocket.py."""
import asyncio
import pytest
from backend.api.websocket import emit, emit_sync, log_bus


class TestEmit:
    @pytest.mark.asyncio
    async def test_puts_message_on_bus(self):
        # Drain any existing messages
        while not log_bus.empty():
            log_bus.get_nowait()

        await emit("test message")
        msg = await asyncio.wait_for(log_bus.get(), timeout=1.0)
        assert msg == "test message"


class TestEmitSync:
    def test_does_not_raise_in_sync_context(self):
        # In a plain sync context (no running loop), emit_sync should not raise
        emit_sync("sync log line")

    def test_does_not_raise_when_called_repeatedly(self):
        for i in range(5):
            emit_sync(f"line {i}")
