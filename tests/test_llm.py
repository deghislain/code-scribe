"""Tests for backend/agent/llm.py — all logic mocked, no real Groq calls."""
import pytest
from unittest.mock import MagicMock, patch, call
from groq import RateLimitError, APIStatusError

import backend.agent.llm as llm_mod


def _make_response(content: str):
    """Build a minimal mock response object matching the Groq SDK structure."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


class TestAsk:
    def _patch_client(self, content="hello"):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_response(content)
        return mock_client

    def test_returns_content_on_success(self, monkeypatch):
        mock_client = self._patch_client("ok response")
        monkeypatch.setattr(llm_mod, "_client", mock_client)
        result = llm_mod.ask("test prompt")
        assert result == "ok response"

    def test_includes_system_message_when_given(self, monkeypatch):
        mock_client = self._patch_client("answer")
        monkeypatch.setattr(llm_mod, "_client", mock_client)
        llm_mod.ask("user prompt", system="you are helpful")
        args = mock_client.chat.completions.create.call_args
        messages = args.kwargs.get("messages") or args.args[0] if args.args else args.kwargs["messages"]
        roles = [m["role"] for m in messages]
        assert "system" in roles

    def test_no_system_message_when_empty(self, monkeypatch):
        mock_client = self._patch_client("answer")
        monkeypatch.setattr(llm_mod, "_client", mock_client)
        llm_mod.ask("user prompt", system="")
        args = mock_client.chat.completions.create.call_args
        messages = args.kwargs["messages"]
        roles = [m["role"] for m in messages]
        assert "system" not in roles

    def test_retries_on_rate_limit_then_succeeds(self, monkeypatch):
        mock_client = MagicMock()
        rate_exc = RateLimitError.__new__(RateLimitError)
        # First two calls raise RateLimitError; third succeeds
        mock_client.chat.completions.create.side_effect = [
            rate_exc,
            rate_exc,
            _make_response("eventual success"),
        ]
        monkeypatch.setattr(llm_mod, "_client", mock_client)
        monkeypatch.setattr(llm_mod.time, "sleep", MagicMock())
        result = llm_mod.ask("prompt")
        assert result == "eventual success"
        assert mock_client.chat.completions.create.call_count == 3

    def test_raises_after_3_retries_on_rate_limit(self, monkeypatch):
        mock_client = MagicMock()
        rate_exc = RateLimitError.__new__(RateLimitError)
        mock_client.chat.completions.create.side_effect = rate_exc
        monkeypatch.setattr(llm_mod, "_client", mock_client)
        monkeypatch.setattr(llm_mod.time, "sleep", MagicMock())
        with pytest.raises(RuntimeError, match="3 retries"):
            llm_mod.ask("prompt")

    def _make_api_status_error(self, status_code: int, message: str = "error"):
        """Create a real APIStatusError instance (needs an httpx.Response)."""
        import httpx
        response = httpx.Response(status_code=status_code, request=httpx.Request("GET", "http://x"))
        return APIStatusError(message, response=response, body=None)

    def test_retries_on_5xx_api_status_error(self, monkeypatch):
        mock_client = MagicMock()
        err = self._make_api_status_error(503, "Service Unavailable")
        mock_client.chat.completions.create.side_effect = [
            err, err, _make_response("recovered")
        ]
        monkeypatch.setattr(llm_mod, "_client", mock_client)
        monkeypatch.setattr(llm_mod.time, "sleep", MagicMock())
        result = llm_mod.ask("prompt")
        assert result == "recovered"

    def test_raises_immediately_on_4xx_api_status_error(self, monkeypatch):
        mock_client = MagicMock()
        err = self._make_api_status_error(400, "Bad request")
        mock_client.chat.completions.create.side_effect = err
        monkeypatch.setattr(llm_mod, "_client", mock_client)
        with pytest.raises(RuntimeError, match="Groq API error 400"):
            llm_mod.ask("prompt")

    def test_raises_on_unexpected_exception(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ValueError("random error")
        monkeypatch.setattr(llm_mod, "_client", mock_client)
        with pytest.raises(RuntimeError, match="Unexpected Groq error"):
            llm_mod.ask("prompt")

    def test_empty_content_returns_empty_string(self, monkeypatch):
        mock_client = self._patch_client(None)  # content is None
        monkeypatch.setattr(llm_mod, "_client", mock_client)
        result = llm_mod.ask("prompt")
        assert result == ""


class TestAskStructured:
    def test_appends_schema_hint(self, monkeypatch):
        captured = []

        def fake_ask(prompt, system="", max_tokens=4096):
            captured.append(prompt)
            return '{"key": "value"}'

        monkeypatch.setattr(llm_mod, "ask", fake_ask)
        llm_mod.ask_structured("Tell me", schema_hint='{"key": "..."}')
        assert "schema" in captured[0].lower() or "JSON" in captured[0]

    def test_no_schema_hint_calls_ask_as_is(self, monkeypatch):
        captured = []

        def fake_ask(prompt, system="", max_tokens=4096):
            captured.append(prompt)
            return "raw"

        monkeypatch.setattr(llm_mod, "ask", fake_ask)
        llm_mod.ask_structured("raw prompt")
        assert captured[0] == "raw prompt"

    def test_returns_raw_string(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "ask", lambda *a, **kw: '{"x": 1}')
        result = llm_mod.ask_structured("prompt", schema_hint="{}")
        assert result == '{"x": 1}'


class TestGetClient:
    def test_creates_client_once(self, monkeypatch):
        monkeypatch.setattr(llm_mod, "_client", None)
        with patch("backend.agent.llm.Groq") as MockGroq:
            MockGroq.return_value = MagicMock()
            c1 = llm_mod._get_client()
            c2 = llm_mod._get_client()
            assert c1 is c2
            MockGroq.assert_called_once()
