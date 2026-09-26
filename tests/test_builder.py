"""Tests for backend/agent/builder.py — subprocess logic mocked."""
import subprocess
import sys
import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path

from backend.agent.builder import (
    _run,
    install_deps,
    build,
    run_tests,
    start_app,
    stop_app,
    _wait_for_port,
    _find_free_port,
)
from backend.memory import store


def _completed(returncode=0, stdout="ok", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class TestRun:
    def test_returns_done_on_zero_exit(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0, "out", ""))
        r = _run(["echo", "hi"], "/tmp")
        assert r["status"] == "done"
        assert r["exit_code"] == 0

    def test_returns_blocked_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(1, "", "err"))
        r = _run(["bad"], "/tmp")
        assert r["status"] == "blocked"
        assert r["exit_code"] == 1

    def test_handles_file_not_found(self, monkeypatch):
        def _raise(*a, **kw):
            raise FileNotFoundError
        monkeypatch.setattr(subprocess, "run", _raise)
        r = _run(["no_such_cmd"], "/tmp")
        assert r["status"] == "blocked"
        assert "not found" in r["stderr"].lower()
        assert r["exit_code"] == -1

    def test_handles_timeout(self, monkeypatch):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="x", timeout=300)
        monkeypatch.setattr(subprocess, "run", _raise)
        r = _run(["slow_cmd"], "/tmp")
        assert r["status"] == "blocked"
        assert "timed out" in r["stderr"].lower()

    def test_stdout_truncated(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0, "x" * 5000, ""))
        r = _run(["cmd"], "/tmp")
        assert len(r["stdout"]) == 4000


class TestInstallDeps:
    def test_python_requirements_txt(self, tmp_path, job_id, monkeypatch):
        (tmp_path / "requirements.txt").write_text("flask\n")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = install_deps(str(tmp_path), {"languages": ["Python"]}, job_id)
        assert r["status"] == "done"

    def test_python_pyproject(self, tmp_path, job_id, monkeypatch):
        (tmp_path / "pyproject.toml").write_text("[project]\n")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = install_deps(str(tmp_path), {"languages": ["Python"]}, job_id)
        assert r["status"] == "done"

    def test_python_no_deps_file(self, tmp_path, job_id):
        r = install_deps(str(tmp_path), {"languages": ["Python"]}, job_id)
        assert r["status"] == "not_applicable"

    def test_javascript_npm(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = install_deps(str(tmp_path), {"languages": ["JavaScript"], "package_managers": ["npm"]}, job_id)
        assert r["status"] == "done"

    def test_javascript_yarn(self, tmp_path, job_id, monkeypatch):
        (tmp_path / "yarn.lock").write_text("")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = install_deps(str(tmp_path), {"languages": ["JavaScript"], "package_managers": ["yarn"]}, job_id)
        assert r["status"] == "done"

    def test_rust_cargo(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = install_deps(str(tmp_path), {"languages": ["Rust"]}, job_id)
        assert r["status"] == "done"

    def test_go(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = install_deps(str(tmp_path), {"languages": ["Go"]}, job_id)
        assert r["status"] == "done"

    def test_unknown_language(self, tmp_path, job_id):
        r = install_deps(str(tmp_path), {"languages": ["COBOL"]}, job_id)
        assert r["status"] == "not_applicable"

    def test_java_maven(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = install_deps(str(tmp_path), {"languages": ["Java"], "build_system": "maven", "package_managers": []}, job_id)
        assert r["status"] == "done"

    def test_java_gradle(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = install_deps(str(tmp_path), {"languages": ["Java"], "build_system": "gradle", "package_managers": []}, job_id)
        assert r["status"] == "done"

    def test_java_unknown_build_system(self, tmp_path, job_id):
        r = install_deps(str(tmp_path), {"languages": ["Java"], "build_system": "unknown", "package_managers": []}, job_id)
        assert r["status"] == "not_applicable"

    def test_checkpoint_saved(self, tmp_path, job_id, monkeypatch):
        (tmp_path / "requirements.txt").write_text("")
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        install_deps(str(tmp_path), {"languages": ["Python"]}, job_id)
        cp = store.get_checkpoint(job_id, "deps_installed")
        assert cp is not None


class TestBuild:
    def test_python_not_applicable(self, tmp_path, job_id):
        r = build(str(tmp_path), {"languages": ["Python"]}, job_id)
        assert r["status"] == "not_applicable"

    def test_js_with_build_script(self, tmp_path, job_id, monkeypatch):
        import json
        (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "tsc"}}))
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = build(str(tmp_path), {"languages": ["JavaScript"]}, job_id)
        assert r["status"] == "done"

    def test_js_no_build_script(self, tmp_path, job_id):
        import json
        (tmp_path / "package.json").write_text(json.dumps({"scripts": {}}))
        r = build(str(tmp_path), {"languages": ["JavaScript"]}, job_id)
        assert r["status"] == "not_applicable"

    def test_js_no_package_json(self, tmp_path, job_id):
        r = build(str(tmp_path), {"languages": ["JavaScript"]}, job_id)
        assert r["status"] == "not_applicable"

    def test_js_invalid_package_json(self, tmp_path, job_id):
        (tmp_path / "package.json").write_text("INVALID")
        r = build(str(tmp_path), {"languages": ["JavaScript"]}, job_id)
        assert r["status"] == "blocked"

    def test_rust(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = build(str(tmp_path), {"languages": ["Rust"]}, job_id)
        assert r["status"] == "done"

    def test_go(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = build(str(tmp_path), {"languages": ["Go"]}, job_id)
        assert r["status"] == "done"

    def test_java_maven(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = build(str(tmp_path), {"languages": ["Java"], "build_system": "maven"}, job_id)
        assert r["status"] == "done"

    def test_unknown_lang(self, tmp_path, job_id):
        r = build(str(tmp_path), {"languages": ["Pascal"]}, job_id)
        assert r["status"] == "not_applicable"

    def test_checkpoint_saved(self, tmp_path, job_id):
        build(str(tmp_path), {"languages": ["Python"]}, job_id)
        cp = store.get_checkpoint(job_id, "build_done")
        assert cp is not None


class TestRunTests:
    def test_python_pytest(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = run_tests(str(tmp_path), {"languages": ["Python"], "test_frameworks": ["pytest"]}, job_id)
        assert r["status"] == "done"

    def test_python_unittest_fallback(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = run_tests(str(tmp_path), {"languages": ["Python"], "test_frameworks": []}, job_id)
        assert r["status"] == "done"

    def test_javascript(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = run_tests(str(tmp_path), {"languages": ["JavaScript"], "test_frameworks": []}, job_id)
        assert r["status"] == "done"

    def test_rust(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = run_tests(str(tmp_path), {"languages": ["Rust"], "test_frameworks": []}, job_id)
        assert r["status"] == "done"

    def test_go(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = run_tests(str(tmp_path), {"languages": ["Go"], "test_frameworks": []}, job_id)
        assert r["status"] == "done"

    def test_unknown_lang(self, tmp_path, job_id):
        r = run_tests(str(tmp_path), {"languages": ["Pascal"], "test_frameworks": []}, job_id)
        assert r["status"] == "not_applicable"

    def test_custom_checkpoint_name(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        run_tests(str(tmp_path), {"languages": ["Python"], "test_frameworks": ["pytest"]}, job_id,
                  checkpoint_name="tests_rerun")
        cp = store.get_checkpoint(job_id, "tests_rerun")
        assert cp is not None

    def test_java_maven(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _completed(0))
        r = run_tests(str(tmp_path), {"languages": ["Java"], "test_frameworks": [], "build_system": "maven"}, job_id)
        assert r["status"] == "done"

    def test_java_no_build_system(self, tmp_path, job_id):
        r = run_tests(str(tmp_path), {"languages": ["Java"], "test_frameworks": [], "build_system": "unknown"}, job_id)
        assert r["status"] == "not_applicable"


class TestStartApp:
    def test_no_cmd_for_unknown_stack(self, tmp_path, job_id):
        proc = start_app(str(tmp_path), {"languages": ["COBOL"], "entry_points": [], "frameworks": []}, job_id)
        assert proc is None
        cp = store.get_checkpoint(job_id, "app_launched")
        assert cp is not None

    def test_flask_launches(self, tmp_path, job_id, monkeypatch):
        (tmp_path / "app.py").write_text("")
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)
        monkeypatch.setattr("backend.agent.builder._wait_for_port", lambda port, timeout=10.0: True)
        proc = start_app(str(tmp_path), {
            "languages": ["Python"],
            "frameworks": ["Flask"],
            "entry_points": ["app.py"],
        }, job_id)
        assert proc is mock_proc

    def test_start_app_exception_returns_none(self, tmp_path, job_id, monkeypatch):
        monkeypatch.setattr(subprocess, "Popen", MagicMock(side_effect=OSError("fail")))
        proc = start_app(str(tmp_path), {
            "languages": ["JavaScript"],
            "frameworks": [],
            "entry_points": [],
        }, job_id)
        assert proc is None

    def test_javascript_npm_start(self, tmp_path, job_id, monkeypatch):
        mock_proc = MagicMock()
        mock_proc.pid = 111
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: mock_proc)
        monkeypatch.setattr("backend.agent.builder._wait_for_port", lambda port, timeout=10.0: False)
        proc = start_app(str(tmp_path), {
            "languages": ["JavaScript"],
            "frameworks": ["React"],   # needs a framework to reach cmd=["npm","start"]
            "entry_points": [],
        }, job_id)
        # Returns proc even when port not bound
        assert proc is mock_proc


class TestStopApp:
    def test_stop_none_is_noop(self):
        stop_app(None)  # should not raise

    def test_terminate_then_wait(self):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = None
        stop_app(mock_proc)
        mock_proc.terminate.assert_called_once()

    def test_kill_on_timeout(self):
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="x", timeout=5), None]
        stop_app(mock_proc)
        mock_proc.kill.assert_called_once()

    def test_oserror_silenced(self):
        mock_proc = MagicMock()
        mock_proc.terminate.side_effect = OSError("already dead")
        stop_app(mock_proc)  # should not raise


class TestHelpers:
    def test_find_free_port_returns_int(self):
        port = _find_free_port()
        assert isinstance(port, int)
        assert port > 0

    def test_wait_for_port_returns_false_on_closed_port(self):
        # Port 1 is never open in tests
        assert _wait_for_port(1, timeout=0.1) is False
