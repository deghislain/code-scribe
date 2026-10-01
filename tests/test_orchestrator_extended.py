"""Extended tests for backend/agent/orchestrator.py — uncovered branches."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.agent import orchestrator
from backend.memory import store


def _make_mock_repo(sha="abc123"):
    mock = MagicMock()
    mock.clone_or_update.return_value = sha
    mock.get_current_sha.return_value = sha
    return mock


def _make_mock_builder(status="done"):
    mock = MagicMock()
    r = {"status": status, "command": "cmd", "stdout": "", "stderr": "", "exit_code": 0}
    mock.install_deps.return_value = r
    mock.build.return_value = r
    mock.run_tests.return_value = r
    mock.start_app.return_value = None
    return mock


def _make_mock_detector():
    mock = MagicMock()
    mock.detect.return_value = {
        "languages": ["Python"],
        "frameworks": [],
        "build_system": "setuptools",
        "test_frameworks": ["pytest"],
        "has_gui": False,
        "gui_type": "none",
        "entry_points": [],
        "package_managers": ["pip"],
    }
    return mock


def _make_all_mocks(monkeypatch, tmp_path):
    monkeypatch.setattr(orchestrator.settings, "WORKSPACE_DIR", str(tmp_path / "workspaces"))
    monkeypatch.setattr(orchestrator.settings, "OUTPUT_DIR", str(tmp_path / "outputs"))

    mock_repo = _make_mock_repo()
    mock_builder = _make_mock_builder()
    mock_detector = _make_mock_detector()

    mock_inspector = MagicMock()
    mock_inspector.inspect.return_value = {"routes": [], "base_url": ""}

    mock_doc_gen = MagicMock()
    mock_doc_gen.generate_user_guide.return_value = "<html>user</html>"
    mock_doc_gen.generate_dev_guide.return_value = "<html>dev</html>"

    mock_pdf = MagicMock()
    mock_injector = MagicMock()

    mock_accept = MagicMock()
    mock_accept.evaluate_acceptance_criteria.return_value = {}

    return {
        "repo": mock_repo,
        "builder": mock_builder,
        "detector": mock_detector,
        "inspector": mock_inspector,
        "doc_generator": mock_doc_gen,
        "pdf_renderer": mock_pdf,
        "chat_injector": mock_injector,
        "acceptance_check": mock_accept,
    }


def _patch_all(mocks):
    """
    Patch by replacing module attributes on the already-imported backend.agent
    package AND updating sys.modules.  This ensures the `from backend.agent import X`
    statements inside orchestrator.run_job() always see the mock regardless of
    whether the real modules were imported by earlier tests in the session.
    """
    import backend.agent as agent_pkg
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        saved = {}
        names = {
            "repo": "repo",
            "builder": "builder",
            "detector": "detector",
            "inspector": "inspector",
            "doc_generator": "doc_generator",
            "pdf_renderer": "pdf_renderer",
            "chat_injector": "chat_injector",
            "acceptance_check": "acceptance_check",
        }
        for key, attr in names.items():
            saved[attr] = getattr(agent_pkg, attr, None)
            setattr(agent_pkg, attr, mocks[key])

        import sys
        saved_sys = {}
        for key, attr in names.items():
            full = f"backend.agent.{attr}"
            saved_sys[full] = sys.modules.get(full)
            sys.modules[full] = mocks[key]

        try:
            yield
        finally:
            for key, attr in names.items():
                if saved[attr] is None:
                    try:
                        delattr(agent_pkg, attr)
                    except AttributeError:
                        pass
                else:
                    setattr(agent_pkg, attr, saved[attr])
            for full, orig in saved_sys.items():
                if orig is None:
                    sys.modules.pop(full, None)
                else:
                    sys.modules[full] = orig

    return _ctx()


class TestRunJobBranchCoverage:
    """Targeted tests for previously-uncovered branches."""

    # --- Step 2 already-done branch (lines 91-93) ---
    def test_resumes_stack_from_existing_checkpoint(self, job_id, monkeypatch, tmp_path):
        """If stack_detected is already done, stack_profile is loaded from the checkpoint evidence."""
        stored_stack = {
            "languages": ["TypeScript"],
            "frameworks": ["React"],
            "build_system": "npm",
            "test_frameworks": ["jest"],
        }
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", stored_stack)
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        # stack detection should NOT be called again
        mocks["detector"].detect.assert_not_called()
        # job ends done
        assert store.get_job(job_id)["status"] == "done"

    # --- Step 2 failure branch (lines 85-86, 88-89) ---
    def test_stack_detection_failure_continues(self, job_id, monkeypatch, tmp_path):
        """Stack detection failure is recorded but job continues."""
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        mocks["detector"].detect.side_effect = RuntimeError("detect error")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "stack_detected")
        assert cp["status"] == "blocked"
        # Job still finishes (does not abort on stack error)
        assert store.get_job(job_id)["status"] == "done"

    # --- Step 3 failure (lines 103-105) ---
    def test_dep_install_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        mocks["builder"].install_deps.side_effect = RuntimeError("install failed")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "deps_installed")
        assert cp["status"] == "blocked"

    # --- Step 4 failure (lines 115-117) ---
    def test_build_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        mocks["builder"].build.side_effect = RuntimeError("build failed")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "build_done")
        assert cp["status"] == "blocked"

    # --- Step 5 failure (lines 127-129) ---
    def test_tests_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        mocks["builder"].run_tests.side_effect = RuntimeError("tests failed")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "tests_run")
        assert cp["status"] == "blocked"

    # --- Step 6 app launch with port (line 144) ---
    def test_app_launched_with_port_sets_base_url(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)

        # builder.start_app sets a checkpoint with a port
        def fake_start_app(repo_dir, stack_profile, job_id):
            store.set_checkpoint(job_id, "app_launched", "done", {"port": 5000})
            return MagicMock()

        mocks["builder"].start_app = fake_start_app

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        # inspector should have been called — base_url would be http://localhost:5000
        mocks["inspector"].inspect.assert_called_once()

    # --- Step 6 already-done with port (lines 150-154) ---
    def test_app_already_launched_restores_base_url(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.set_checkpoint(job_id, "app_launched", "done", {"port": 8080})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        # start_app must NOT be called again
        mocks["builder"].start_app.assert_not_called()
        # job completes
        assert store.get_job(job_id)["status"] == "done"

    # --- Step 6 failure (lines 146-148) ---
    def test_app_launch_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        mocks["builder"].start_app.side_effect = RuntimeError("launch failed")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "app_launched")
        assert cp["status"] == "blocked"

    # --- Step 7 already-done with routes (lines 170-173) ---
    def test_gui_already_inspected_restores_ui_map(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.set_checkpoint(job_id, "app_launched", "done", {})
        store.set_checkpoint(job_id, "gui_inspected", "done", {
            "routes": ["/", "/about"],
            "base_url": "http://localhost:3000",
        })
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        # GUI inspection should NOT be called again
        mocks["inspector"].inspect.assert_not_called()
        assert store.get_job(job_id)["status"] == "done"

    # --- Step 7 failure (lines 166-168) ---
    def test_gui_inspection_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.set_checkpoint(job_id, "app_launched", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        mocks["inspector"].inspect.side_effect = RuntimeError("inspect failed")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "gui_inspected")
        assert cp["status"] == "blocked"

    # --- Step 8 failure (lines 188-190) ---
    def test_user_guide_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.set_checkpoint(job_id, "app_launched", "done", {})
        store.set_checkpoint(job_id, "gui_inspected", "done", {"routes": [], "base_url": ""})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        mocks["doc_generator"].generate_user_guide.side_effect = RuntimeError("user guide error")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "user_guide_generated")
        assert cp["status"] == "blocked"

    # --- Step 9 failure (lines 205-207) ---
    def test_dev_guide_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.set_checkpoint(job_id, "app_launched", "done", {})
        store.set_checkpoint(job_id, "gui_inspected", "done", {"routes": [], "base_url": ""})
        store.set_checkpoint(job_id, "user_guide_generated", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        mocks["doc_generator"].generate_dev_guide.side_effect = RuntimeError("dev guide error")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "dev_guide_generated")
        assert cp["status"] == "blocked"

    # --- Step 10 failure (lines 217-219) ---
    def test_chat_injection_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.set_checkpoint(job_id, "app_launched", "done", {})
        store.set_checkpoint(job_id, "gui_inspected", "done", {"routes": [], "base_url": ""})
        store.set_checkpoint(job_id, "user_guide_generated", "done", {})
        store.set_checkpoint(job_id, "dev_guide_generated", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        mocks["chat_injector"].inject.side_effect = RuntimeError("inject failed")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "chat_view_injected")
        assert cp["status"] == "blocked"

    # --- Step 11 failure (lines 229-231) ---
    def test_retest_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        # tests_run is pre-set as done, so run_tests is called ONLY for tests_rerun.
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.set_checkpoint(job_id, "app_launched", "done", {})
        store.set_checkpoint(job_id, "gui_inspected", "done", {"routes": [], "base_url": ""})
        store.set_checkpoint(job_id, "user_guide_generated", "done", {})
        store.set_checkpoint(job_id, "dev_guide_generated", "done", {})
        store.set_checkpoint(job_id, "chat_view_injected", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        # run_tests is only called once (for tests_rerun step), make it fail.
        mocks["builder"].run_tests.side_effect = RuntimeError("retest failed")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "tests_rerun")
        assert cp is not None
        assert cp["status"] == "blocked"

    # --- Stop app when started (lines 236-240) ---
    def test_stop_app_called_when_app_was_launched(self, job_id, monkeypatch, tmp_path):
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)
        fake_proc = MagicMock()
        mocks["builder"].start_app.return_value = fake_proc
        # store the app_launched checkpoint inside start_app
        def fake_start_app(repo_dir, stack, jid):
            store.set_checkpoint(jid, "app_launched", "done", {})
            return fake_proc
        mocks["builder"].start_app = fake_start_app

        stop_called = []
        def fake_stop_app(proc):
            stop_called.append(proc)
        mocks["builder"].stop_app = fake_stop_app

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        assert len(stop_called) == 1
        assert stop_called[0] is fake_proc

    # --- Step 12 failure (lines 249-251) ---
    def test_memory_save_failure_is_recorded(self, job_id, monkeypatch, tmp_path):
        # Pre-complete all previous steps
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.set_checkpoint(job_id, "app_launched", "done", {})
        store.set_checkpoint(job_id, "gui_inspected", "done", {"routes": [], "base_url": ""})
        store.set_checkpoint(job_id, "user_guide_generated", "done", {})
        store.set_checkpoint(job_id, "dev_guide_generated", "done", {})
        store.set_checkpoint(job_id, "chat_view_injected", "done", {})
        store.set_checkpoint(job_id, "tests_rerun", "done", {})
        store.update_job_commit(job_id, "abc")

        mocks = _make_all_mocks(monkeypatch, tmp_path)

        with _patch_all(mocks), \
             patch.object(orchestrator, "_save_output_schema", side_effect=RuntimeError("schema fail")):
            orchestrator.run_job(job_id)

        cp = store.get_checkpoint(job_id, "memory_saved")
        assert cp["status"] == "blocked"
        # Job still ends as done despite that non-critical failure
        assert store.get_job(job_id)["status"] == "done"


class TestRenderDocumentKwargs:
    """Verify _render_document passes version + generated_at through to render_pdf."""

    def test_render_pdf_called_with_version_and_generated_at(self, job_id, monkeypatch, tmp_path):
        """render_pdf must receive version= and generated_at= on a fresh save (version==1)."""
        mocks = _make_all_mocks(monkeypatch, tmp_path)

        called_kwargs = {}

        def capture_render(html, path, title="", generated_at="", version=1):
            called_kwargs["title"] = title
            called_kwargs["generated_at"] = generated_at
            called_kwargs["version"] = version

        mocks["pdf_renderer"].render_pdf.side_effect = capture_render

        # Pre-complete all steps up to and including step 7 so we land in steps 8+9.
        store.set_checkpoint(job_id, "repo_cloned", "done", {"sha": "abc"})
        store.set_checkpoint(job_id, "stack_detected", "done", _make_mock_detector().detect.return_value)
        store.set_checkpoint(job_id, "deps_installed", "done", {})
        store.set_checkpoint(job_id, "build_done", "done", {})
        store.set_checkpoint(job_id, "tests_run", "done", {})
        store.set_checkpoint(job_id, "app_launched", "done", {})
        store.set_checkpoint(job_id, "gui_inspected", "done", {"routes": [], "base_url": ""})
        store.update_job_commit(job_id, "abc")

        with _patch_all(mocks):
            orchestrator.run_job(job_id)

        # render_pdf is called twice per document (probe + final), so 4 total
        # for user_guide + dev_guide.
        assert mocks["pdf_renderer"].render_pdf.call_count == 4

        # Inspect the final call for user_guide (index 1 — the version-stamped render)
        final_call = mocks["pdf_renderer"].render_pdf.call_args_list[1]
        kw = final_call.kwargs if final_call.kwargs else {}
        args = final_call.args if final_call.args else final_call[0]

        # version kwarg on the final render must be an integer ≥ 1
        version_val = kw.get("version") if kw else None
        if version_val is None and len(args) >= 5:
            version_val = args[4]
        assert isinstance(version_val, int) and version_val >= 1

        # generated_at must be a non-empty string
        gen_at = kw.get("generated_at") if kw else None
        if gen_at is None and len(args) >= 4:
            gen_at = args[3]
        assert isinstance(gen_at, str) and len(gen_at) > 0

    def test_second_render_increments_version(self, job_id, monkeypatch, tmp_path):
        """Calling _render_document twice for the same doc_type stamps version=2 on the second final render."""
        mocks = _make_all_mocks(monkeypatch, tmp_path)
        output_dir = tmp_path / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)

        versions_seen = []

        def capture_version(html, path, title="", generated_at="", version=1):
            versions_seen.append(version)

        mocks["pdf_renderer"].render_pdf.side_effect = capture_version

        out_path = str(output_dir / f"{job_id}_user_guide.pdf")

        with _patch_all(mocks):
            orchestrator._render_document("<html/>", out_path, "End User Guide", job_id, "user_guide")
            orchestrator._render_document("<html/>", out_path, "End User Guide", job_id, "user_guide")

        # Each _render_document call does: probe (version=0) + final (version=N).
        # So two calls → [0, 1, 0, 2] — the final versions are at indices 1 and 3.
        assert versions_seen == [0, 1, 0, 2]
        assert versions_seen[1] == 1   # first doc: version 1
        assert versions_seen[3] == 2   # second doc (same type): version 2


class TestSaveOutputSchema:
    def test_writes_json_file(self, job_id, monkeypatch, tmp_path):
        stack_profile = {"languages": ["Python"], "frameworks": []}
        checkpoints = [{"checkpoint_name": "build_done", "status": "done"}]

        mock_accept = MagicMock()
        mock_accept.evaluate_acceptance_criteria.return_value = {"all_passed": True}

        import backend.agent as _agent_pkg
        import sys
        saved = getattr(_agent_pkg, "acceptance_check", None)
        saved_sys = sys.modules.get("backend.agent.acceptance_check")
        setattr(_agent_pkg, "acceptance_check", mock_accept)
        sys.modules["backend.agent.acceptance_check"] = mock_accept
        try:
            orchestrator._save_output_schema(
                job_id,
                "https://github.com/user/repo",
                stack_profile,
                checkpoints,
                tmp_path,
            )
        finally:
            if saved is None:
                try:
                    delattr(_agent_pkg, "acceptance_check")
                except AttributeError:
                    pass
            else:
                setattr(_agent_pkg, "acceptance_check", saved)
            if saved_sys is None:
                sys.modules.pop("backend.agent.acceptance_check", None)
            else:
                sys.modules["backend.agent.acceptance_check"] = saved_sys

        out_file = tmp_path / f"{job_id}_summary.json"
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert data["job_id"] == job_id
        assert data["repo_url"] == "https://github.com/user/repo"
        assert "stack_profile" in data
        assert "checkpoints" in data

class TestCancelRunningJobs:
    def test_cancel_does_nothing_when_no_job(self):
        """cancel_running_jobs() is a no-op when no job is registered."""
        orchestrator._unregister_job()
        orchestrator._stop_event.clear()
        # Should not raise
        orchestrator.cancel_running_jobs()

    def test_cancel_sets_stop_event_and_marks_blocked(self, job_id):
        """cancel_running_jobs() sets the stop event and marks the job blocked."""
        orchestrator._register_job(job_id)
        assert not orchestrator._stop_event.is_set()

        orchestrator.cancel_running_jobs()

        assert orchestrator._stop_event.is_set()
        updated = store.get_job(job_id)
        assert updated["status"] == "blocked"

    def test_cancel_terminates_app_proc(self, job_id):
        """cancel_running_jobs() terminates a registered app subprocess."""
        mock_proc = MagicMock()
        orchestrator._register_job(job_id)
        orchestrator.register_app_proc(mock_proc)

        orchestrator.cancel_running_jobs()

        mock_proc.terminate.assert_called_once()

    def test_check_stop_returns_true_when_event_set(self, job_id):
        """_check_stop() returns True and marks the job blocked when stop event is set."""
        store.update_job_status(job_id, "running")
        orchestrator._register_job(job_id)
        orchestrator._stop_event.set()

        result = orchestrator._check_stop(job_id)

        assert result is True
        assert store.get_job(job_id)["status"] == "blocked"
        orchestrator._stop_event.clear()

    def test_check_stop_returns_false_when_event_clear(self, job_id):
        """_check_stop() returns False when no shutdown has been requested."""
        orchestrator._register_job(job_id)
        orchestrator._stop_event.clear()

        result = orchestrator._check_stop(job_id)

        assert result is False

