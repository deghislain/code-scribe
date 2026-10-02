"""Tests for backend/agent/chat_injector.py."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.agent import chat_injector
from backend.memory import store


class TestCollectSourceSnippets:
    def test_returns_empty_string_for_missing_dir(self, tmp_path):
        result = chat_injector._collect_source_snippets(str(tmp_path / "nonexistent"), {"frameworks": []})
        assert result == ""

    def test_collects_html_for_flask(self, tmp_path):
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "base.html").write_text("<html>hello</html>")
        result = chat_injector._collect_source_snippets(str(tmp_path), {"frameworks": ["Flask"]})
        assert "base.html" in result
        assert "hello" in result

    def test_collects_jsx_for_react(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "App.jsx").write_text("export default function App() { return <div/>; }")
        result = chat_injector._collect_source_snippets(str(tmp_path), {"frameworks": ["React"]})
        assert "App.jsx" in result

    def test_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("should be skipped")
        result = chat_injector._collect_source_snippets(str(tmp_path), {"frameworks": []})
        assert "should be skipped" not in result

    def test_respects_max_files_limit(self, tmp_path):
        for i in range(10):
            (tmp_path / f"file{i}.html").write_text(f"content{i}")
        result = chat_injector._collect_source_snippets(str(tmp_path), {"frameworks": ["Flask"]})
        # Should include at most _MAX_SNIPPET_FILES file headers
        assert result.count("---") <= chat_injector._MAX_SNIPPET_FILES * 2

    def test_snippets_truncated_to_max_chars(self, tmp_path):
        (tmp_path / "big.html").write_text("x" * 2000)
        result = chat_injector._collect_source_snippets(str(tmp_path), {"frameworks": ["Flask"]})
        # Each file's content should be capped at _MAX_SNIPPET_CHARS
        assert "x" * (chat_injector._MAX_SNIPPET_CHARS + 1) not in result


class TestGenerateChatWidgetCode:
    def _fake_llm(self, content):
        return lambda *a, **kw: content

    def test_react_returns_jsx_file(self, monkeypatch):
        response = json.dumps({"ChatWidget.jsx": "<div>chat</div>"})
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", self._fake_llm(response))
        result = chat_injector.generate_chat_widget_code(
            {"frameworks": ["React"]}, {"routes": []}
        )
        assert "ChatWidget.jsx" in result

    def test_vue_returns_vue_file(self, monkeypatch):
        response = json.dumps({"ChatWidget.vue": "<template>chat</template>"})
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", self._fake_llm(response))
        result = chat_injector.generate_chat_widget_code(
            {"frameworks": ["Vue"]}, {"routes": []}
        )
        assert "ChatWidget.vue" in result

    def test_flask_returns_html_snippet(self, monkeypatch):
        response = json.dumps({"chat_widget.html": "<div>widget</div>"})
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", self._fake_llm(response))
        result = chat_injector.generate_chat_widget_code(
            {"frameworks": ["Flask"]}, {"routes": []}
        )
        assert "chat_widget.html" in result

    def test_streamlit_treated_as_python_web(self, monkeypatch):
        """Streamlit apps should get the HTML widget path (no dedicated component format)."""
        response = json.dumps({"chat_widget.html": "<div>st-widget</div>"})
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", self._fake_llm(response))
        result = chat_injector.generate_chat_widget_code(
            {"frameworks": ["Streamlit"]}, {"routes": []}
        )
        assert "chat_widget.html" in result

    def test_unknown_stack_html_fallback(self, monkeypatch):
        response = json.dumps({"chat_widget.html": "<div>generic</div>"})
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", self._fake_llm(response))
        result = chat_injector.generate_chat_widget_code(
            {"frameworks": []}, {"routes": []}
        )
        assert "chat_widget.html" in result

    def test_bad_json_response_falls_back(self, monkeypatch):
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", self._fake_llm("not json"))
        result = chat_injector.generate_chat_widget_code({"frameworks": []}, {})
        assert "chat_widget.html" in result
        assert result["chat_widget.html"] == "not json"

    def test_nextjs_treated_as_react(self, monkeypatch):
        response = json.dumps({"ChatWidget.jsx": "<div/>"})
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", self._fake_llm(response))
        result = chat_injector.generate_chat_widget_code(
            {"frameworks": ["Next.js"]}, {"routes": []}
        )
        assert "ChatWidget.jsx" in result

    def test_source_snippets_included_in_prompt(self, tmp_path, monkeypatch):
        """Prompt sent to LLM must contain source file content when repo_dir is provided."""
        (tmp_path / "app.py").write_text("# main entry point")
        captured: list[str] = []
        monkeypatch.setattr(
            "backend.agent.chat_injector.llm.ask",
            lambda prompt, **kw: (captured.append(prompt), json.dumps({"chat_widget.html": "x"}))[1],
        )
        chat_injector.generate_chat_widget_code(
            {"frameworks": ["Flask"]}, {"routes": []}, repo_dir=str(tmp_path)
        )
        assert captured, "LLM was never called"
        assert "main entry point" in captured[0]

    def test_no_source_snippets_when_no_repo_dir(self, monkeypatch):
        """When repo_dir is omitted the source_block must be absent from the prompt."""
        captured: list[str] = []
        monkeypatch.setattr(
            "backend.agent.chat_injector.llm.ask",
            lambda prompt, **kw: (captured.append(prompt), json.dumps({"chat_widget.html": "x"}))[1],
        )
        chat_injector.generate_chat_widget_code({"frameworks": ["Flask"]}, {"routes": []})
        assert "source snippets" not in captured[0]


class TestInjectIntoApp:
    def test_writes_widget_files_for_flask(self, tmp_path):
        (tmp_path / "templates").mkdir()
        widget_files = {"chat_widget.html": "<div>chat</div>"}
        with patch("backend.agent.chat_injector._find_shell_file", return_value=None):
            modified = chat_injector.inject_into_app(str(tmp_path), widget_files, {"frameworks": ["Flask"]})
        written = tmp_path / "templates" / "chat_widget.html"
        assert written.exists()
        assert "templates/chat_widget.html" in modified

    def test_writes_widget_files_for_react(self, tmp_path):
        widget_files = {"ChatWidget.jsx": "export default function ChatWidget() {}"}
        with patch("backend.agent.chat_injector._find_shell_file", return_value=None):
            modified = chat_injector.inject_into_app(str(tmp_path), widget_files, {"frameworks": ["React"]})
        written = tmp_path / "src" / "components" / "ChatWidget.jsx"
        assert written.exists()


class TestFindShellFile:
    def test_react_finds_app_jsx(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "App.jsx").write_text("<App/>")
        result = chat_injector._find_shell_file(tmp_path, {"frameworks": ["React"]})
        assert result == "src/App.jsx"

    def test_vue_finds_app_vue(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "App.vue").write_text("<template/>")
        result = chat_injector._find_shell_file(tmp_path, {"frameworks": ["Vue"]})
        assert result == "src/App.vue"

    def test_returns_none_when_not_found(self, tmp_path):
        result = chat_injector._find_shell_file(tmp_path, {"frameworks": ["React"]})
        assert result is None

    def test_flask_finds_base_html(self, tmp_path):
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "base.html").write_text("<!DOCTYPE html>")
        result = chat_injector._find_shell_file(tmp_path, {"frameworks": ["Flask"]})
        assert result is not None and result.endswith("base.html")

    def test_fastapi_finds_base_html(self, tmp_path):
        """FastAPI apps with HTML templates must resolve their shell file correctly."""
        tmpl = tmp_path / "templates"
        tmpl.mkdir()
        (tmpl / "base.html").write_text("<!DOCTYPE html>")
        result = chat_injector._find_shell_file(tmp_path, {"frameworks": ["FastAPI"]})
        assert result is not None and result.endswith("base.html")

    def test_fastapi_no_templates_returns_none(self, tmp_path):
        result = chat_injector._find_shell_file(tmp_path, {"frameworks": ["FastAPI"]})
        assert result is None

    def test_generic_finds_index_html(self, tmp_path):
        (tmp_path / "index.html").write_text("<!DOCTYPE html>")
        result = chat_injector._find_shell_file(tmp_path, {"frameworks": []})
        assert result == "index.html"


class TestAddChatApiEndpoint:
    def test_fastapi_writes_file(self, tmp_path):
        result = chat_injector.add_chat_api_endpoint(str(tmp_path), {"frameworks": ["FastAPI"]})
        assert result == "chat_endpoint.py"
        assert (tmp_path / "chat_endpoint.py").exists()

    def test_flask_writes_blueprint(self, tmp_path):
        result = chat_injector.add_chat_api_endpoint(str(tmp_path), {"frameworks": ["Flask"]})
        assert result == "chat_blueprint.py"
        assert (tmp_path / "chat_blueprint.py").exists()

    def test_django_writes_view(self, tmp_path):
        result = chat_injector.add_chat_api_endpoint(str(tmp_path), {"frameworks": ["Django"]})
        assert result == "chat_view.py"
        assert (tmp_path / "chat_view.py").exists()
        content = (tmp_path / "chat_view.py").read_text()
        assert "JsonResponse" in content
        assert "/api/chat" in content

    def test_nextjs_writes_api_route(self, tmp_path):
        result = chat_injector.add_chat_api_endpoint(str(tmp_path), {"frameworks": ["Next.js"]})
        assert result == "pages/api/chat.js"
        assert (tmp_path / "pages" / "api" / "chat.js").exists()
        content = (tmp_path / "pages" / "api" / "chat.js").read_text()
        assert "handler" in content
        assert "/api/chat" in content

    def test_express_writes_route_file(self, tmp_path):
        result = chat_injector.add_chat_api_endpoint(str(tmp_path), {"frameworks": ["Express"]})
        assert result == "chat_route.js"
        assert (tmp_path / "chat_route.js").exists()
        content = (tmp_path / "chat_route.js").read_text()
        assert "router" in content
        assert "/api/chat" in content

    def test_unknown_stack_returns_none(self, tmp_path):
        result = chat_injector.add_chat_api_endpoint(str(tmp_path), {"frameworks": []})
        assert result is None

    def test_streamlit_returns_none(self, tmp_path):
        """Streamlit widget calls Code-Scribe directly — no separate endpoint file."""
        result = chat_injector.add_chat_api_endpoint(str(tmp_path), {"frameworks": ["Streamlit"]})
        assert result is None


# ---------------------------------------------------------------------------
# Streamlit-specific tests
# ---------------------------------------------------------------------------

class TestFindStreamlitEntry:
    def test_canonical_name_wins(self, tmp_path):
        """app.py is always priority 1."""
        (tmp_path / "app.py").write_text("import streamlit as st")
        result = chat_injector._find_streamlit_entry(tmp_path, {"entry_points": []})
        assert result == "app.py"

    def test_main_guard_beats_plain_import(self, tmp_path):
        """A file with __name__=='__main__' wins over a helper that just imports st."""
        (tmp_path / "helper.py").write_text("import streamlit as st\nst.write('x')")
        (tmp_path / "quizzer.py").write_text(
            'import streamlit as st\n\nif __name__ == "__main__":\n    st.title("Q")\n'
        )
        result = chat_injector._find_streamlit_entry(tmp_path, {"entry_points": []})
        assert result == "quizzer.py"

    def test_falls_back_to_st_call_file(self, tmp_path):
        """No __main__ guard → pick any file that imports AND calls st.*."""
        (tmp_path / "runner.py").write_text("import streamlit as st\nst.title('hi')")
        result = chat_injector._find_streamlit_entry(tmp_path, {"entry_points": []})
        assert result == "runner.py"

    def test_falls_back_to_plain_import(self, tmp_path):
        """Last resort: a file that only imports streamlit."""
        (tmp_path / "widget.py").write_text("import streamlit as st")
        result = chat_injector._find_streamlit_entry(tmp_path, {"entry_points": []})
        assert result == "widget.py"

    def test_returns_none_when_no_match(self, tmp_path):
        result = chat_injector._find_streamlit_entry(tmp_path, {"entry_points": []})
        assert result is None


class TestGenerateStreamlitWidget:
    def _meta(self, app_name="Quizer", app_desc="A quiz app."):
        return json.dumps({"app_name": app_name, "app_description": app_desc})

    def test_returns_python_source(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: self._meta())
        code = chat_injector._generate_streamlit_widget(
            str(tmp_path), {"frameworks": ["Streamlit"]}, {}, job_id=42
        )
        assert "render_chat_widget" in code
        assert "Quizer" in code
        assert "42" in code  # job_id substituted
        assert "st.chat_input" in code
        assert "cs_chat_history" in code

    def test_handles_bad_llm_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: "not json")
        code = chat_injector._generate_streamlit_widget(
            str(tmp_path), {"frameworks": ["Streamlit"]}, {}, job_id=1
        )
        assert "render_chat_widget" in code

    def test_widget_contains_guard_comment(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: self._meta())
        code = chat_injector._generate_streamlit_widget(
            str(tmp_path), {"frameworks": ["Streamlit"]}, {}, job_id=1
        )
        assert chat_injector._STREAMLIT_WIDGET_GUARD in code

    def test_maintains_session_memory(self, tmp_path, monkeypatch):
        """Generated widget must accumulate conversation history across turns."""
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: self._meta())
        code = chat_injector._generate_streamlit_widget(
            str(tmp_path), {"frameworks": ["Streamlit"]}, {}, job_id=1
        )
        assert "cs_chat_history" in code
        assert "history_text" in code
        # cs_pending_input is the two-phase rerun mechanism for multi-turn
        assert "cs_pending_input" in code

    def test_widget_is_focused_on_app_guidance(self, tmp_path, monkeypatch):
        """APP_CONTEXT must include app description so answers are app-scoped."""
        app_desc = "Upload a PDF, pick a topic, generate MCQ questions."
        monkeypatch.setattr(
            "backend.agent.chat_injector.llm.ask",
            lambda *a, **kw: json.dumps({"app_name": "Quizer", "app_description": app_desc}),
        )
        code = chat_injector._generate_streamlit_widget(
            str(tmp_path), {"frameworks": ["Streamlit"]}, {}, job_id=1
        )
        assert app_desc in code

    def test_url_uses_configured_port(self, tmp_path, monkeypatch):
        """The default fallback URL must contain the code_scribe_port argument."""
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: self._meta())
        code = chat_injector._generate_streamlit_widget(
            str(tmp_path), {"frameworks": ["Streamlit"]}, {}, job_id=1,
            code_scribe_port=8001,
        )
        assert "8001" in code

    def test_url_falls_back_to_env_var(self, tmp_path, monkeypatch):
        """Widget must check CODE_SCRIBE_URL env var at runtime before using the default."""
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: self._meta())
        code = chat_injector._generate_streamlit_widget(
            str(tmp_path), {"frameworks": ["Streamlit"]}, {}, job_id=1,
        )
        assert "CODE_SCRIBE_URL" in code
        assert "os.environ" in code

    def test_no_inline_message_rendering_after_input(self, tmp_path, monkeypatch):
        """After st.chat_input there must be NO st.chat_message call — display only in history loop."""
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: self._meta())
        code = chat_injector._generate_streamlit_widget(
            str(tmp_path), {"frameworks": ["Streamlit"]}, {}, job_id=1,
        )
        # The only rendering of messages should be in the history loop,
        # which comes BEFORE st.chat_input in the source.
        input_pos = code.index("st.chat_input")
        # No st.chat_message should appear after the chat_input call
        assert "st.chat_message" not in code[input_pos:]

    def test_rerun_triggered_on_submit(self, tmp_path, monkeypatch):
        """Widget must call st.rerun() after receiving user input to avoid mixed display."""
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: self._meta())
        code = chat_injector._generate_streamlit_widget(
            str(tmp_path), {"frameworks": ["Streamlit"]}, {}, job_id=1,
        )
        assert "st.rerun()" in code


class TestInjectStreamlit:
    def _fake_llm(self, monkeypatch):
        meta = json.dumps({"app_name": "Quizer", "app_description": "A quiz app."})
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: meta)

    def test_writes_widget_file(self, tmp_path, monkeypatch):
        self._fake_llm(monkeypatch)
        (tmp_path / "quizzer.py").write_text("import streamlit as st\nst.title('hi')")
        modified = chat_injector._inject_streamlit(
            str(tmp_path), {"frameworks": ["Streamlit"], "entry_points": ["quizzer.py"]}, {}, job_id=1
        )
        assert chat_injector._STREAMLIT_WIDGET_FILENAME in modified
        assert (tmp_path / chat_injector._STREAMLIT_WIDGET_FILENAME).exists()

    def test_wires_into_entry_point(self, tmp_path, monkeypatch):
        self._fake_llm(monkeypatch)
        (tmp_path / "app.py").write_text("import streamlit as st\nst.title('hello')")
        chat_injector._inject_streamlit(
            str(tmp_path), {"frameworks": ["Streamlit"], "entry_points": []}, {}, job_id=1
        )
        content = (tmp_path / "app.py").read_text()
        assert "render_chat_widget" in content
        assert "from chat_widget_st import render_chat_widget" in content

    def test_injection_is_idempotent(self, tmp_path, monkeypatch):
        """Running inject twice must not duplicate the wiring call."""
        self._fake_llm(monkeypatch)
        (tmp_path / "app.py").write_text("import streamlit as st")
        chat_injector._inject_streamlit(
            str(tmp_path), {"frameworks": ["Streamlit"], "entry_points": []}, {}, job_id=1
        )
        chat_injector._inject_streamlit(
            str(tmp_path), {"frameworks": ["Streamlit"], "entry_points": []}, {}, job_id=1
        )
        content = (tmp_path / "app.py").read_text()
        assert content.count("render_chat_widget()") == 1

    def test_no_entry_point_still_writes_widget(self, tmp_path, monkeypatch):
        """Even if no entry point is found, the widget module is still written."""
        self._fake_llm(monkeypatch)
        modified = chat_injector._inject_streamlit(
            str(tmp_path), {"frameworks": ["Streamlit"], "entry_points": []}, {}, job_id=1
        )
        assert chat_injector._STREAMLIT_WIDGET_FILENAME in modified


class TestInject:
    def test_no_gui_sets_not_applicable(self, tmp_path, job_id):
        chat_injector.inject(str(tmp_path), {"has_gui": False}, {}, job_id)
        cp = store.get_checkpoint(job_id, "chat_view_injected")
        assert cp["status"] == "not_applicable"

    def test_full_pipeline_runs(self, tmp_path, job_id, monkeypatch):
        widget_code = json.dumps({"chat_widget.html": "<div>chat</div>"})
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: widget_code)
        with patch("backend.agent.chat_injector._find_shell_file", return_value=None):
            chat_injector.inject(str(tmp_path), {"has_gui": True, "frameworks": []}, {}, job_id)
        cp = store.get_checkpoint(job_id, "chat_view_injected")
        assert cp["status"] == "done"

    def test_streamlit_pipeline_runs(self, tmp_path, job_id, monkeypatch):
        """inject() must use the Streamlit-specific path for Streamlit apps."""
        meta = json.dumps({"app_name": "Quizer", "app_description": "A quiz app."})
        monkeypatch.setattr("backend.agent.chat_injector.llm.ask", lambda *a, **kw: meta)
        (tmp_path / "quizzer.py").write_text("import streamlit as st\nst.title('hi')")
        chat_injector.inject(
            str(tmp_path),
            {"has_gui": True, "frameworks": ["Streamlit"], "entry_points": ["quizzer.py"]},
            {},
            job_id,
        )
        cp = store.get_checkpoint(job_id, "chat_view_injected")
        assert cp["status"] == "done"
        assert chat_injector._STREAMLIT_WIDGET_FILENAME in cp["evidence"]["modified_files"]
        assert (tmp_path / chat_injector._STREAMLIT_WIDGET_FILENAME).exists()
