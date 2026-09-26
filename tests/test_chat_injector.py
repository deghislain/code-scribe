"""Tests for backend/agent/chat_injector.py."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.agent import chat_injector
from backend.memory import store


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

    def test_unknown_stack_returns_none(self, tmp_path):
        result = chat_injector.add_chat_api_endpoint(str(tmp_path), {"frameworks": []})
        assert result is None


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
