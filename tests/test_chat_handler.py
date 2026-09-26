"""Tests for backend/agent/chat_handler.py."""
import pytest
from unittest.mock import patch, MagicMock
from backend.agent import chat_handler
from backend.memory import store


class TestAnswerQuestion:
    def _stub_llm(self, response_text, monkeypatch):
        monkeypatch.setattr("backend.agent.chat_handler.llm.ask", lambda *a, **kw: response_text)

    def test_returns_llm_response(self, job_id, monkeypatch):
        self._stub_llm("The answer is 42.", monkeypatch)
        result = chat_handler.answer_question(job_id, "What is the answer?")
        assert result == "The answer is 42."

    def test_grounded_response_saves_learned_knowledge(self, job_id, monkeypatch):
        self._stub_llm("The main module is app.py.", monkeypatch)
        chat_handler.answer_question(job_id, "Where is the main module?")
        knowledge = store.get_learned_knowledge(job_id)
        assert len(knowledge) == 1
        assert knowledge[0]["question"] == "Where is the main module?"

    def test_grounded_response_saves_user_interaction_validated(self, job_id, monkeypatch):
        self._stub_llm("It uses FastAPI.", monkeypatch)
        chat_handler.answer_question(job_id, "What framework?")
        interactions = store.get_user_interactions(job_id)
        assert len(interactions) == 1
        assert interactions[0]["validated"] == 1

    def test_ungrounded_response_not_saved_to_knowledge(self, job_id, monkeypatch):
        self._stub_llm("I cannot verify this from the codebase.", monkeypatch)
        chat_handler.answer_question(job_id, "What does baz() do?")
        knowledge = store.get_learned_knowledge(job_id)
        assert len(knowledge) == 0

    def test_ungrounded_response_saves_unvalidated_interaction(self, job_id, monkeypatch):
        self._stub_llm("I cannot verify this from the codebase.", monkeypatch)
        chat_handler.answer_question(job_id, "Unknown question")
        interactions = store.get_user_interactions(job_id)
        assert interactions[0]["validated"] == 0

    def test_uses_relevant_knowledge_in_prompt(self, job_id, monkeypatch):
        # Pre-seed knowledge
        store.save_learned_knowledge(job_id, "What is foo?", "Foo is a thing.", confidence=0.9)

        captured_prompts = []

        def fake_ask(prompt, system="", max_tokens=1024):
            captured_prompts.append(prompt)
            return "ok"

        monkeypatch.setattr("backend.agent.chat_handler.llm.ask", fake_ask)
        chat_handler.answer_question(job_id, "What is foo?")
        assert "Foo is a thing." in captured_prompts[0]

    def test_checkpoint_summary_included_in_prompt(self, job_id, monkeypatch):
        store.set_checkpoint(job_id, "build_done", "done")
        captured = []

        def fake_ask(prompt, system="", max_tokens=1024):
            captured.append(prompt)
            return "answer"

        monkeypatch.setattr("backend.agent.chat_handler.llm.ask", fake_ask)
        chat_handler.answer_question(job_id, "Is it built?")
        assert "build_done" in captured[0]
