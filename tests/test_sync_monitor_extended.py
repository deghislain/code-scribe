"""Extended tests for backend/agent/sync_monitor.py — _regenerate coverage."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

import backend.agent.sync_monitor as sm
from backend.memory import store


class TestRegenerate:
    """Tests for the _regenerate private function."""

    def _make_mock_detector(self) -> MagicMock:
        mock = MagicMock()
        mock.detect.return_value = {
            "languages": ["Python"],
            "frameworks": [],
            "build_system": "setuptools",
            "test_frameworks": ["pytest"],
        }
        return mock

    def _patch_agent_modules(self, mock_detector, mock_doc_gen, mock_pdf):
        """
        Patch agent sub-modules both in sys.modules AND as attributes on backend.agent,
        so that lazy `from backend.agent import X` imports inside _regenerate always pick
        up the mock regardless of whether the real modules were imported earlier.
        """
        import backend.agent as _agent_pkg
        import contextlib, sys

        @contextlib.contextmanager
        def _ctx():
            mapping = {
                "detector": mock_detector,
                "doc_generator": mock_doc_gen,
                "pdf_renderer": mock_pdf,
            }
            saved_attr = {k: getattr(_agent_pkg, k, None) for k in mapping}
            saved_sys = {f"backend.agent.{k}": sys.modules.get(f"backend.agent.{k}") for k in mapping}

            for k, v in mapping.items():
                setattr(_agent_pkg, k, v)
                sys.modules[f"backend.agent.{k}"] = v
            try:
                yield
            finally:
                for k in mapping:
                    if saved_attr[k] is None:
                        try:
                            delattr(_agent_pkg, k)
                        except AttributeError:
                            pass
                    else:
                        setattr(_agent_pkg, k, saved_attr[k])
                    orig = saved_sys[f"backend.agent.{k}"]
                    if orig is None:
                        sys.modules.pop(f"backend.agent.{k}", None)
                    else:
                        sys.modules[f"backend.agent.{k}"] = orig

        return _ctx()

    def test_regenerates_dev_guide_only(self, job_id, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.agent.sync_monitor.settings.OUTPUT_DIR", str(tmp_path))

        mock_detector = self._make_mock_detector()
        mock_doc_gen = MagicMock()
        mock_doc_gen.generate_dev_guide.return_value = "<html>dev</html>"
        mock_pdf = MagicMock()

        with self._patch_agent_modules(mock_detector, mock_doc_gen, mock_pdf):
            sm._regenerate(job_id, str(tmp_path), {"dev_guide"}, "newsha")

        mock_doc_gen.generate_dev_guide.assert_called_once()
        mock_pdf.render_pdf.assert_called_once()
        # _regenerate itself calls store.save_document internally; confirm the DB row exists
        doc = store.get_documents(job_id)
        assert any(d["doc_type"] == "dev_guide" for d in doc)

    def test_render_pdf_receives_version_and_generated_at(self, job_id, tmp_path, monkeypatch):
        """_regenerate must forward version= and generated_at= to render_pdf."""
        monkeypatch.setattr("backend.agent.sync_monitor.settings.OUTPUT_DIR", str(tmp_path))

        mock_detector = self._make_mock_detector()
        mock_doc_gen = MagicMock()
        mock_doc_gen.generate_dev_guide.return_value = "<html>dev</html>"
        mock_pdf = MagicMock()

        captured = {}

        def capture_render(html, path, title="", generated_at="", version=1):
            captured["version"] = version
            captured["generated_at"] = generated_at

        mock_pdf.render_pdf.side_effect = capture_render

        with self._patch_agent_modules(mock_detector, mock_doc_gen, mock_pdf):
            sm._regenerate(job_id, str(tmp_path), {"dev_guide"}, "newsha")

        assert isinstance(captured.get("version"), int) and captured["version"] >= 1
        assert isinstance(captured.get("generated_at"), str) and len(captured["generated_at"]) > 0

    def test_second_regenerate_increments_version(self, job_id, tmp_path, monkeypatch):
        """Each subsequent _regenerate call for the same doc_type increments version."""
        monkeypatch.setattr("backend.agent.sync_monitor.settings.OUTPUT_DIR", str(tmp_path))

        mock_detector = self._make_mock_detector()
        mock_doc_gen = MagicMock()
        mock_doc_gen.generate_dev_guide.return_value = "<html>dev</html>"
        mock_pdf = MagicMock()

        versions_seen = []

        def capture_version(html, path, title="", generated_at="", version=1):
            versions_seen.append(version)

        mock_pdf.render_pdf.side_effect = capture_version

        with self._patch_agent_modules(mock_detector, mock_doc_gen, mock_pdf):
            sm._regenerate(job_id, str(tmp_path), {"dev_guide"}, "sha1")
            sm._regenerate(job_id, str(tmp_path), {"dev_guide"}, "sha2")

        assert versions_seen == [1, 2]

    def test_regenerates_user_guide_with_existing_gui_checkpoint(self, job_id, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.agent.sync_monitor.settings.OUTPUT_DIR", str(tmp_path))

        # Set up a gui_inspected checkpoint with route evidence
        store.set_checkpoint(job_id, "gui_inspected", "done", {
            "routes": ["/home", "/about"],
            "base_url": "http://localhost:3000",
        })

        mock_detector = self._make_mock_detector()
        mock_doc_gen = MagicMock()
        mock_doc_gen.generate_user_guide.return_value = "<html>user</html>"
        mock_pdf = MagicMock()

        with self._patch_agent_modules(mock_detector, mock_doc_gen, mock_pdf):
            sm._regenerate(job_id, str(tmp_path), {"user_guide"}, "newsha")

        mock_doc_gen.generate_user_guide.assert_called_once()
        call_args = mock_doc_gen.generate_user_guide.call_args
        ui_map = call_args[0][2]  # positional arg index 2 is ui_map
        assert len(ui_map["routes"]) == 2

    def test_regenerates_user_guide_without_gui_checkpoint(self, job_id, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.agent.sync_monitor.settings.OUTPUT_DIR", str(tmp_path))

        mock_detector = self._make_mock_detector()
        mock_doc_gen = MagicMock()
        mock_doc_gen.generate_user_guide.return_value = "<html>user</html>"
        mock_pdf = MagicMock()

        with self._patch_agent_modules(mock_detector, mock_doc_gen, mock_pdf):
            sm._regenerate(job_id, str(tmp_path), {"user_guide"}, "newsha")

        mock_doc_gen.generate_user_guide.assert_called_once()
        call_args = mock_doc_gen.generate_user_guide.call_args
        ui_map = call_args[0][2]
        assert ui_map["routes"] == []

    def test_regenerates_both_guides(self, job_id, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.agent.sync_monitor.settings.OUTPUT_DIR", str(tmp_path))

        mock_detector = self._make_mock_detector()
        mock_doc_gen = MagicMock()
        mock_doc_gen.generate_dev_guide.return_value = "<html>dev</html>"
        mock_doc_gen.generate_user_guide.return_value = "<html>user</html>"
        mock_pdf = MagicMock()

        with self._patch_agent_modules(mock_detector, mock_doc_gen, mock_pdf):
            sm._regenerate(job_id, str(tmp_path), {"dev_guide", "user_guide"}, "newsha")

        mock_doc_gen.generate_dev_guide.assert_called_once()
        mock_doc_gen.generate_user_guide.assert_called_once()
        assert mock_pdf.render_pdf.call_count == 2

    def test_no_sections_does_nothing(self, job_id, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.agent.sync_monitor.settings.OUTPUT_DIR", str(tmp_path))

        mock_detector = self._make_mock_detector()
        mock_doc_gen = MagicMock()
        mock_pdf = MagicMock()

        with self._patch_agent_modules(mock_detector, mock_doc_gen, mock_pdf):
            sm._regenerate(job_id, str(tmp_path), set(), "newsha")

        mock_doc_gen.generate_dev_guide.assert_not_called()
        mock_doc_gen.generate_user_guide.assert_not_called()
