"""Tests for backend/agent/inspector.py."""
import json
import re
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.agent.inspector import (
    _extract_flask_routes,
    _extract_fastapi_routes,
    _extract_django_routes,
    _extract_react_router_routes,
    extract_routes_static,
    extract_routes_runtime,
    describe_ui_with_llm,
    inspect,
    UIMap,
    RouteInfo,
)
from backend.memory import store


class TestExtractFlaskRoutes:
    def test_finds_route_decorator(self, tmp_path):
        (tmp_path / "app.py").write_text("@app.route('/hello')\ndef hello(): pass\n")
        routes = _extract_flask_routes(str(tmp_path))
        assert "/hello" in routes

    def test_finds_blueprint_route(self, tmp_path):
        (tmp_path / "views.py").write_text("@bp.route('/items')\ndef items(): pass\n")
        routes = _extract_flask_routes(str(tmp_path))
        assert "/items" in routes

    def test_empty_when_no_routes(self, tmp_path):
        (tmp_path / "app.py").write_text("def hello(): pass\n")
        assert _extract_flask_routes(str(tmp_path)) == []


class TestExtractFastapiRoutes:
    def test_finds_get_route(self, tmp_path):
        (tmp_path / "main.py").write_text('@router.get("/users")\ndef users(): pass\n')
        routes = _extract_fastapi_routes(str(tmp_path))
        assert "/users" in routes

    def test_finds_post_route(self, tmp_path):
        (tmp_path / "main.py").write_text('@app.post("/submit")\ndef submit(): pass\n')
        routes = _extract_fastapi_routes(str(tmp_path))
        assert "/submit" in routes


class TestExtractDjangoRoutes:
    def test_finds_path_call(self, tmp_path):
        (tmp_path / "urls.py").write_text("urlpatterns = [path('admin/', admin.site.urls)]\n")
        routes = _extract_django_routes(str(tmp_path))
        assert "/admin/" in routes

    def test_finds_url_call(self, tmp_path):
        # url() with a plain string (no r prefix) so the regex captures it
        (tmp_path / "urls.py").write_text("urlpatterns = [url('^about/$', views.about)]\n")
        routes = _extract_django_routes(str(tmp_path))
        assert any("about" in r for r in routes)


class TestExtractReactRouterRoutes:
    def test_finds_path_prop(self, tmp_path):
        (tmp_path / "App.jsx").write_text('<Route path="/home" component={Home} />\n')
        routes = _extract_react_router_routes(str(tmp_path))
        assert "/home" in routes

    def test_ignores_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "lib"
        nm.mkdir(parents=True)
        (nm / "routes.js").write_text('<Route path="/secret" />\n')
        routes = _extract_react_router_routes(str(tmp_path))
        assert "/secret" not in routes

    def test_only_absolute_paths_included(self, tmp_path):
        (tmp_path / "App.jsx").write_text('const path = "relative/path";\n')
        routes = _extract_react_router_routes(str(tmp_path))
        assert "relative/path" not in routes


class TestExtractRoutesStatic:
    def test_flask(self, tmp_path):
        (tmp_path / "app.py").write_text("@app.route('/ping')\ndef ping(): pass\n")
        ui_map = extract_routes_static(str(tmp_path), {"frameworks": ["Flask"]})
        paths = [r["path"] for r in ui_map["routes"]]
        assert "/ping" in paths

    def test_deduplication(self, tmp_path):
        (tmp_path / "a.py").write_text("@app.route('/dup')\ndef a(): pass\n")
        (tmp_path / "b.py").write_text("@app.route('/dup')\ndef b(): pass\n")
        ui_map = extract_routes_static(str(tmp_path), {"frameworks": ["Flask"]})
        paths = [r["path"] for r in ui_map["routes"]]
        assert paths.count("/dup") == 1

    def test_no_framework_returns_empty(self, tmp_path):
        ui_map = extract_routes_static(str(tmp_path), {"frameworks": []})
        assert ui_map["routes"] == []


class TestExtractRoutesRuntime:
    def test_crawls_base_url(self):
        mock_resp = MagicMock()
        mock_resp.text = '<html><title>Home</title><a href="/about">About</a></html>'

        with patch("backend.agent.inspector.httpx.get", return_value=mock_resp):
            ui_map = extract_routes_runtime("http://localhost:8080")

        paths = [r["path"] for r in ui_map["routes"]]
        assert "/" in paths or "" in paths

    def test_exception_silenced(self):
        with patch("backend.agent.inspector.httpx.get", side_effect=Exception("conn refused")):
            ui_map = extract_routes_runtime("http://localhost:9999")
        assert ui_map["routes"] == []


class TestDescribeUiWithLlm:
    def test_empty_routes_returns_unchanged(self):
        ui_map = UIMap(routes=[], base_url="")
        result = describe_ui_with_llm("/tmp", {}, ui_map)
        assert result["routes"] == []

    def test_enriches_with_llm_response(self, tmp_path):
        ui_map = UIMap(
            routes=[RouteInfo(path="/home", title="", description="")],
            base_url="",
        )
        llm_reply = json.dumps([{"path": "/home", "title": "Home", "description": "The home page."}])
        with patch("backend.agent.inspector.llm.ask", return_value=llm_reply):
            result = describe_ui_with_llm(str(tmp_path), {"frameworks": ["Flask"]}, ui_map)
        assert result["routes"][0]["description"] == "The home page."

    def test_bad_llm_json_returns_original(self, tmp_path):
        ui_map = UIMap(
            routes=[RouteInfo(path="/x", title="", description="")],
            base_url="",
        )
        with patch("backend.agent.inspector.llm.ask", return_value="not json"):
            result = describe_ui_with_llm(str(tmp_path), {}, ui_map)
        assert result is ui_map

    def test_llm_reply_with_code_fences_parsed(self, tmp_path):
        ui_map = UIMap(
            routes=[RouteInfo(path="/shop", title="", description="")],
            base_url="",
        )
        raw = "```json\n" + json.dumps([{"path": "/shop", "title": "Shop", "description": "Buy things."}]) + "\n```"
        with patch("backend.agent.inspector.llm.ask", return_value=raw):
            result = describe_ui_with_llm(str(tmp_path), {}, ui_map)
        assert result["routes"][0]["description"] == "Buy things."


class TestInspect:
    def test_no_gui_returns_empty(self, job_id):
        ui_map = inspect("/tmp", {"has_gui": False}, job_id)
        assert ui_map["routes"] == []
        cp = store.get_checkpoint(job_id, "gui_inspected")
        assert cp["status"] == "not_applicable"

    def test_with_gui_calls_static_analysis(self, tmp_path, job_id):
        (tmp_path / "app.py").write_text("@app.route('/home')\ndef home(): pass\n")
        with patch("backend.agent.inspector.describe_ui_with_llm") as mock_desc:
            mock_desc.side_effect = lambda repo_dir, sp, ui_map: ui_map
            ui_map = inspect(str(tmp_path), {"has_gui": True, "frameworks": ["Flask"]}, job_id)
        assert any(r["path"] == "/home" for r in ui_map["routes"])
        cp = store.get_checkpoint(job_id, "gui_inspected")
        assert cp["status"] == "done"

    def test_merges_runtime_routes(self, tmp_path, job_id):
        runtime_ui = UIMap(
            routes=[RouteInfo(path="/runtime", title="", description="")],
            base_url="http://localhost:8080",
        )
        with patch("backend.agent.inspector.extract_routes_runtime", return_value=runtime_ui), \
             patch("backend.agent.inspector.describe_ui_with_llm", side_effect=lambda r, s, u: u):
            ui_map = inspect(str(tmp_path), {"has_gui": True, "frameworks": []},
                             job_id, base_url="http://localhost:8080")
        paths = [r["path"] for r in ui_map["routes"]]
        assert "/runtime" in paths
