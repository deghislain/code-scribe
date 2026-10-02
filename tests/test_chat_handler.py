"""Tests for backend/agent/chat_handler.py."""
import pytest
from unittest.mock import patch, MagicMock
from backend.agent import chat_handler
from backend.memory import store

_NO_WEB = {"title": "", "url": "", "snippet": "", "page_text": ""}


class TestAnswerQuestion:
    def _stub_llm(self, response_text, monkeypatch):
        monkeypatch.setattr("backend.agent.chat_handler.llm.ask", lambda *a, **kw: response_text)

    def _stub_no_web(self, monkeypatch):
        """Prevent real HTTP calls in tests that don't exercise the web path."""
        monkeypatch.setattr(
            "backend.agent.chat_handler.web_search.search_and_fetch",
            lambda *a, **kw: [],
        )

    def test_returns_llm_response(self, job_id, monkeypatch):
        self._stub_no_web(monkeypatch)
        self._stub_llm("The answer is 42.", monkeypatch)
        result = chat_handler.answer_question(job_id, "What is the answer?")
        assert result == "The answer is 42."

    def test_grounded_response_saves_learned_knowledge(self, job_id, monkeypatch):
        self._stub_no_web(monkeypatch)
        self._stub_llm("The main module is app.py.", monkeypatch)
        chat_handler.answer_question(job_id, "Where is the main module?")
        knowledge = store.get_learned_knowledge(job_id)
        assert len(knowledge) == 1
        assert knowledge[0]["question"] == "Where is the main module?"

    def test_grounded_response_saves_user_interaction_validated(self, job_id, monkeypatch):
        self._stub_no_web(monkeypatch)
        self._stub_llm("It uses FastAPI.", monkeypatch)
        chat_handler.answer_question(job_id, "What framework?")
        interactions = store.get_user_interactions(job_id)
        assert len(interactions) == 1
        assert interactions[0]["validated"] == 1

    def test_ungrounded_repo_triggers_web_search(self, job_id, monkeypatch):
        """When the LLM says it cannot verify from the repo, web search is called."""
        web_called = []

        def fake_search(question, max_results=3):
            web_called.append(question)
            return [{"title": "Guide", "url": "https://example.com", "snippet": "s", "page_text": "step 1 install"}]

        monkeypatch.setattr("backend.agent.chat_handler.web_search.search_and_fetch", fake_search)

        call_count = [0]

        def fake_ask(prompt, system="", max_tokens=1024):
            call_count[0] += 1
            if call_count[0] == 1:
                return "I cannot verify this from the codebase."
            return "Here is the web-sourced answer.\n\nSources:\nhttps://example.com"

        monkeypatch.setattr("backend.agent.chat_handler.llm.ask", fake_ask)
        result = chat_handler.answer_question(job_id, "How to install ollama?")

        assert web_called, "web_search.search_and_fetch was not called"
        assert "Sources:" in result

    def test_web_answer_saved_as_grounded(self, job_id, monkeypatch):
        """A web-sourced answer (which doesn't contain the unverifiable phrase) is stored."""
        monkeypatch.setattr(
            "backend.agent.chat_handler.web_search.search_and_fetch",
            lambda *a, **kw: [{"title": "T", "url": "https://x.com", "snippet": "", "page_text": "info"}],
        )
        call_count = [0]

        def fake_ask(prompt, system="", max_tokens=1024):
            call_count[0] += 1
            if call_count[0] == 1:
                return "I cannot verify this from the codebase."
            return "Web answer here.\n\nSources:\nhttps://x.com"

        monkeypatch.setattr("backend.agent.chat_handler.llm.ask", fake_ask)
        chat_handler.answer_question(job_id, "Install steps?")
        knowledge = store.get_learned_knowledge(job_id)
        assert len(knowledge) == 1

    def test_web_search_no_results_returns_fallback_message(self, job_id, monkeypatch):
        """If web search returns nothing, a clear fallback message is returned."""
        monkeypatch.setattr(
            "backend.agent.chat_handler.web_search.search_and_fetch",
            lambda *a, **kw: [],
        )
        self._stub_llm("I cannot verify this from the codebase.", monkeypatch)
        result = chat_handler.answer_question(job_id, "Some obscure question")
        assert "could not find" in result.lower()

    def test_uses_relevant_knowledge_in_prompt(self, job_id, monkeypatch):
        self._stub_no_web(monkeypatch)
        store.save_learned_knowledge(job_id, "What is foo?", "Foo is a thing.", confidence=0.9)

        captured_prompts = []

        def fake_ask(prompt, system="", max_tokens=1024):
            captured_prompts.append(prompt)
            return "ok"

        monkeypatch.setattr("backend.agent.chat_handler.llm.ask", fake_ask)
        chat_handler.answer_question(job_id, "What is foo?")
        assert "Foo is a thing." in captured_prompts[0]

    def test_checkpoint_summary_included_in_prompt(self, job_id, monkeypatch):
        self._stub_no_web(monkeypatch)
        store.set_checkpoint(job_id, "build_done", "done")
        captured = []

        def fake_ask(prompt, system="", max_tokens=1024):
            captured.append(prompt)
            return "answer"

        monkeypatch.setattr("backend.agent.chat_handler.llm.ask", fake_ask)
        chat_handler.answer_question(job_id, "Is it built?")
        assert "build_done" in captured[0]


class TestWebSearchAnswer:
    def test_builds_prompt_from_results(self, monkeypatch):
        """_web_search_answer passes source content to the LLM."""
        monkeypatch.setattr(
            "backend.agent.chat_handler.web_search.search_and_fetch",
            lambda *a, **kw: [
                {"title": "Ollama Docs", "url": "https://ollama.com/docs", "snippet": "", "page_text": "Run: ollama install"},
            ],
        )
        captured = []
        monkeypatch.setattr(
            "backend.agent.chat_handler.llm.ask",
            lambda prompt, system="", max_tokens=1024: captured.append(prompt) or "answer",
        )
        chat_handler._web_search_answer("How to install ollama?")
        assert "ollama.com/docs" in captured[0]
        assert "ollama install" in captured[0]

    def test_returns_fallback_when_no_results(self, monkeypatch):
        monkeypatch.setattr(
            "backend.agent.chat_handler.web_search.search_and_fetch",
            lambda *a, **kw: [],
        )
        result = chat_handler._web_search_answer("anything")
        assert "could not find" in result.lower()


class TestWebSearch:
    def test_search_returns_empty_on_http_error(self, monkeypatch):
        """search() returns [] gracefully when the HTTP request fails."""
        import httpx
        from backend.agent import web_search

        def raise_error(*a, **kw):
            raise httpx.ConnectError("timeout")

        monkeypatch.setattr("backend.agent.web_search.httpx.post", raise_error)
        assert web_search.search("anything") == []

    def test_fetch_page_text_returns_empty_on_error(self, monkeypatch):
        import httpx
        from backend.agent import web_search

        monkeypatch.setattr(
            "backend.agent.web_search.httpx.get",
            lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("x")),
        )
        assert web_search.fetch_page_text("https://example.com") == ""

    def test_strip_tags_removes_html(self):
        from backend.agent import web_search
        assert web_search._strip_tags("<b>hello</b> <i>world</i>") == "hello world"

    def test_strip_tags_decodes_entities(self):
        from backend.agent import web_search
        assert "&amp;" not in web_search._strip_tags("a &amp; b")
