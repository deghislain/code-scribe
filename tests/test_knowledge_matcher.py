"""Tests for backend/agent/knowledge_matcher.py"""
import pytest
from backend.agent.knowledge_matcher import _tokenize, find_relevant


class TestTokenize:
    def test_basic(self):
        assert _tokenize("Hello World") == {"hello", "world"}

    def test_empty(self):
        assert _tokenize("") == set()

    def test_special_chars_stripped(self):
        tokens = _tokenize("foo-bar baz!")
        assert "foo" in tokens
        assert "bar" in tokens
        assert "baz" in tokens

    def test_lowercased(self):
        assert _tokenize("UPPER lower") == {"upper", "lower"}


class TestFindRelevant:
    def _make_knowledge(self, *questions):
        return [{"question": q, "answer": f"A: {q}", "id": i} for i, q in enumerate(questions)]

    def test_empty_knowledge_returns_empty(self):
        assert find_relevant("what is foo?", []) == []

    def test_empty_question_returns_empty(self):
        kb = self._make_knowledge("what is foo?")
        assert find_relevant("", kb) == []

    def test_exact_match_ranked_first(self):
        kb = self._make_knowledge("what does foo do", "how to run bar", "foo function")
        results = find_relevant("what does foo do", kb)
        assert results[0]["question"] == "what does foo do"

    def test_no_overlap_returns_empty(self):
        kb = self._make_knowledge("how does bar work")
        results = find_relevant("completely unrelated xyz123", kb)
        assert results == []

    def test_top_k_respected(self):
        kb = self._make_knowledge(*[f"foo question {i}" for i in range(10)])
        results = find_relevant("foo question", kb, top_k=3)
        assert len(results) <= 3

    def test_sorted_by_overlap_descending(self):
        kb = self._make_knowledge(
            "foo bar baz",          # 3 token overlap with "foo bar baz qux"
            "foo bar",              # 2 token overlap
            "foo",                  # 1 token overlap
        )
        results = find_relevant("foo bar baz qux", kb)
        assert results[0]["question"] == "foo bar baz"

    def test_missing_question_key_handled(self):
        kb = [{"answer": "some answer", "id": 1}]  # no 'question' key
        # Should not raise; empty question → 0 overlap
        results = find_relevant("test", kb)
        assert results == []
