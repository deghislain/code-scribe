"""Tests for backend/agent/doc_generator.py — all LLM and store calls mocked."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

import backend.agent.doc_generator as dg
from backend.memory import store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stack() -> dict:
    return {
        "languages": ["Python"],
        "frameworks": ["FastAPI"],
        "build_system": "setuptools",
        "test_frameworks": ["pytest"],
    }


def _make_checkpoints() -> list[dict]:
    return [
        {"checkpoint_name": "tests_run", "status": "done",
         "evidence": {"exit_code": 0, "stdout": "5 passed"}},
        {"checkpoint_name": "build_done", "status": "done",
         "evidence": {"exit_code": 0, "stdout": "ok"}},
    ]


# ---------------------------------------------------------------------------
# _read_safe
# ---------------------------------------------------------------------------

class TestReadSafe:
    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "README.md"
        f.write_text("hello world")
        assert dg._read_safe(f) == "hello world"

    def test_truncates_to_max_chars(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("a" * 5000)
        result = dg._read_safe(f, max_chars=100)
        assert len(result) == 100

    def test_returns_empty_on_missing_file(self, tmp_path):
        assert dg._read_safe(tmp_path / "nonexistent.txt") == ""


# ---------------------------------------------------------------------------
# _tree_listing
# ---------------------------------------------------------------------------

class TestTreeListing:
    def test_lists_files(self, tmp_path):
        (tmp_path / "main.py").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "util.py").write_text("y")
        result = dg._tree_listing(tmp_path)
        assert "main.py" in result

    def test_skips_hidden_and_cache(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "a.pyc").write_text("")
        result = dg._tree_listing(tmp_path)
        assert ".git" not in result
        assert "__pycache__" not in result

    def test_truncates_after_max_files(self, tmp_path):
        for i in range(15):
            (tmp_path / f"file_{i}.py").write_text("")
        result = dg._tree_listing(tmp_path, max_files=5)
        assert "truncated" in result


# ---------------------------------------------------------------------------
# _section
# ---------------------------------------------------------------------------

class TestSection:
    def test_wraps_in_section_tag(self):
        html = dg._section("My Title", "<p>body</p>")
        assert "<h2>My Title</h2>" in html
        assert "<p>body</p>" in html
        assert html.startswith("<section>")
        assert html.strip().endswith("</section>")


# ---------------------------------------------------------------------------
# _llm_section
# ---------------------------------------------------------------------------

class TestLlmSection:
    def test_calls_llm_ask_and_returns_result(self):
        with patch("backend.agent.doc_generator.llm.ask", return_value="<p>result</p>") as mock_ask:
            result = dg._llm_section("Architecture", "evidence text", "Write about it")
        assert result == "<p>result</p>"
        mock_ask.assert_called_once()
        # Verify the section title is injected into the prompt
        call_args = mock_ask.call_args[0][0]
        assert "Architecture" in call_args


# ---------------------------------------------------------------------------
# _run_sections_parallel
# ---------------------------------------------------------------------------

class TestRunSectionsParallel:
    def test_returns_results_for_all_tasks(self):
        tasks = [
            (0, "Sec A", "ev", "instr", 512),
            (1, "Sec B", "ev", "instr", 512),
        ]
        call_count = [0]

        def fake_ask(prompt, max_tokens=1024):
            call_count[0] += 1
            return f"<p>body {call_count[0]}</p>"

        with patch("backend.agent.doc_generator.llm.ask", side_effect=fake_ask):
            results = dg._run_sections_parallel(tasks)

        assert set(results.keys()) == {0, 1}
        assert all(r.startswith("<p>") for r in results.values())


# ---------------------------------------------------------------------------
# generate_user_guide
# ---------------------------------------------------------------------------

class TestGenerateUserGuide:
    def test_returns_html_with_all_sections(self, job_id, tmp_repo):
        stack = _make_stack()
        ui_map = {"routes": [{"path": "/home", "description": "Home page"}]}
        checkpoints = _make_checkpoints()

        (tmp_repo / "README.md").write_text("# Test App\nA simple test app.")

        with patch("backend.agent.doc_generator.llm.ask", return_value="<p>content</p>"):
            html = dg.generate_user_guide(job_id, stack, ui_map, checkpoints, str(tmp_repo))

        assert "<section>" in html
        assert "Overview" in html
        # checkpoint is now set by the orchestrator after PDF rendering, not here
        cp = store.get_checkpoint(job_id, "user_guide_generated")
        assert cp is None

    def test_no_routes_uses_unverified_placeholder(self, job_id, tmp_repo):
        stack = _make_stack()
        ui_map = {"routes": []}
        checkpoints = _make_checkpoints()

        with patch("backend.agent.doc_generator.llm.ask", return_value="<p>content</p>"):
            html = dg.generate_user_guide(job_id, stack, ui_map, checkpoints, str(tmp_repo))

        assert "UNVERIFIED" in html


# ---------------------------------------------------------------------------
# generate_dev_guide
# ---------------------------------------------------------------------------

class TestGenerateDevGuide:
    def test_returns_html_with_all_sections(self, job_id, tmp_repo):
        stack = _make_stack()
        checkpoints = _make_checkpoints()

        (tmp_repo / "README.md").write_text("# Dev App")
        (tmp_repo / "requirements.txt").write_text("fastapi\n")

        with patch("backend.agent.doc_generator.llm.ask", return_value="<p>dev content</p>"):
            html = dg.generate_dev_guide(job_id, stack, checkpoints, str(tmp_repo))

        assert "<section>" in html
        assert "Project Overview" in html
        assert "Changelog" in html

        # checkpoint is now set by the orchestrator after PDF rendering, not here
        cp = store.get_checkpoint(job_id, "dev_guide_generated")
        assert cp is None

    def test_falls_back_to_rst_readme(self, job_id, tmp_repo):
        stack = _make_stack()
        checkpoints = []

        (tmp_repo / "README.rst").write_text("RST readme content")

        with patch("backend.agent.doc_generator.llm.ask", return_value="<p>ok</p>"):
            html = dg.generate_dev_guide(job_id, stack, checkpoints, str(tmp_repo))

        assert "Project Overview" in html
