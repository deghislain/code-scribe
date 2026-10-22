"""Tests for backend/agent/llm — all logic mocked, no real Groq calls."""
import pytest
from unittest.mock import MagicMock, patch
from groq import RateLimitError, APIStatusError

import backend.agent.llm as llm_mod
from backend.agent.llm.groq_provider import GroqProvider


def _make_response(content: str):
    """Build a minimal mock response object matching the Groq SDK structure."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_api_status_error(status_code: int, message: str = "error"):
    """Create a real APIStatusError instance (needs an httpx.Response)."""
    import httpx
    response = httpx.Response(
        status_code=status_code, request=httpx.Request("GET", "http://x")
    )
    return APIStatusError(message, response=response, body=None)


# ---------------------------------------------------------------------------
# Helpers to patch the provider used by the module-level ask()
# ---------------------------------------------------------------------------

def _make_provider_with_client(content: str) -> GroqProvider:
    """Return a GroqProvider whose internal Groq client returns *content*."""
    provider = GroqProvider()
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_response(content)
    provider._client = mock_client
    return provider


class TestAsk:
    def test_returns_content_on_success(self, monkeypatch):
        provider = _make_provider_with_client("ok response")
        monkeypatch.setattr(llm_mod, "_provider", provider)
        result = llm_mod.ask("test prompt")
        assert result == "ok response"

    def test_includes_system_message_when_given(self, monkeypatch):
        provider = _make_provider_with_client("answer")
        monkeypatch.setattr(llm_mod, "_provider", provider)
        llm_mod.ask("user prompt", system="you are helpful")
        args = provider._client.chat.completions.create.call_args
        messages = args.kwargs.get("messages") or args.args[0]
        roles = [m["role"] for m in messages]
        assert "system" in roles

    def test_no_system_message_when_empty(self, monkeypatch):
        provider = _make_provider_with_client("answer")
        monkeypatch.setattr(llm_mod, "_provider", provider)
        llm_mod.ask("user prompt", system="")
        args = provider._client.chat.completions.create.call_args
        messages = args.kwargs["messages"]
        roles = [m["role"] for m in messages]
        assert "system" not in roles

    def test_retries_on_rate_limit_then_succeeds(self, monkeypatch):
        provider = GroqProvider()
        mock_client = MagicMock()
        rate_exc = RateLimitError.__new__(RateLimitError)
        mock_client.chat.completions.create.side_effect = [
            rate_exc,
            rate_exc,
            _make_response("eventual success"),
        ]
        provider._client = mock_client
        monkeypatch.setattr(llm_mod, "_provider", provider)
        with patch("backend.agent.llm.groq_provider.time") as mock_time:
            result = llm_mod.ask("prompt")
        assert result == "eventual success"
        assert mock_client.chat.completions.create.call_count == 3

    def test_raises_after_3_retries_on_rate_limit(self, monkeypatch):
        provider = GroqProvider()
        mock_client = MagicMock()
        rate_exc = RateLimitError.__new__(RateLimitError)
        mock_client.chat.completions.create.side_effect = rate_exc
        provider._client = mock_client
        monkeypatch.setattr(llm_mod, "_provider", provider)
        with patch("backend.agent.llm.groq_provider.time") as mock_time:
            with pytest.raises(RuntimeError, match="3 retries"):
                llm_mod.ask("prompt")

    def test_retries_on_5xx_api_status_error(self, monkeypatch):
        provider = GroqProvider()
        mock_client = MagicMock()
        err = _make_api_status_error(503, "Service Unavailable")
        mock_client.chat.completions.create.side_effect = [
            err, err, _make_response("recovered")
        ]
        provider._client = mock_client
        monkeypatch.setattr(llm_mod, "_provider", provider)
        with patch("backend.agent.llm.groq_provider.time") as mock_time:
            result = llm_mod.ask("prompt")
        assert result == "recovered"

    def test_raises_immediately_on_4xx_api_status_error(self, monkeypatch):
        provider = GroqProvider()
        mock_client = MagicMock()
        err = _make_api_status_error(400, "Bad request")
        mock_client.chat.completions.create.side_effect = err
        provider._client = mock_client
        monkeypatch.setattr(llm_mod, "_provider", provider)
        with pytest.raises(RuntimeError, match="Groq API error 400"):
            llm_mod.ask("prompt")

    def test_raises_on_unexpected_exception(self, monkeypatch):
        provider = GroqProvider()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ValueError("random error")
        provider._client = mock_client
        monkeypatch.setattr(llm_mod, "_provider", provider)
        with pytest.raises(RuntimeError, match="Unexpected Groq error"):
            llm_mod.ask("prompt")

    def test_empty_content_returns_empty_string(self, monkeypatch):
        provider = _make_provider_with_client(None)
        monkeypatch.setattr(llm_mod, "_provider", provider)
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


class TestProviderFactory:
    def test_groq_provider_is_default(self, monkeypatch):
        """The factory returns a GroqProvider when LLM_PROVIDER='groq'."""
        # Reset the cached provider so the factory runs fresh
        monkeypatch.setattr(llm_mod, "_provider", None)
        with patch("backend.agent.llm.groq_provider.Groq"):
            provider = llm_mod._get_provider()
        assert isinstance(provider, GroqProvider)

    def test_unknown_provider_raises(self, monkeypatch):
        from backend.config import settings
        monkeypatch.setattr(settings, "LLM_PROVIDER", "unknown_provider")
        monkeypatch.setattr(llm_mod, "_provider", None)
        with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
            llm_mod._get_provider()
        # Restore so other tests in the session are unaffected
        monkeypatch.setattr(llm_mod, "_provider", None)
