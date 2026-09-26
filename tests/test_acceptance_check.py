"""Tests for backend/agent/acceptance_check.py"""
import pytest
from backend.agent.acceptance_check import evaluate_acceptance_criteria
from backend.memory import store


class TestEvaluateAcceptanceCriteria:
    def _set_done(self, job_id, *names):
        for name in names:
            store.set_checkpoint(job_id, name, "done")

    def _set_blocked(self, job_id, *names):
        for name in names:
            store.set_checkpoint(job_id, name, "blocked")

    def test_all_pass_when_all_checkpoints_done(self, job_id):
        cp_names = [
            "repo_cloned", "stack_detected", "deps_installed",
            "build_done", "tests_run", "app_launched", "gui_inspected",
            "user_guide_generated", "dev_guide_generated",
            "chat_view_injected", "tests_rerun", "memory_saved",
        ]
        self._set_done(job_id, *cp_names)
        store.save_document(job_id, "user_guide", "/a.pdf")

        result = evaluate_acceptance_criteria(job_id)

        for key, val in result.items():
            assert val == "pass", f"{key} should be pass"

    def test_fail_when_checkpoint_missing(self, job_id):
        # Only set some checkpoints
        self._set_done(job_id, "repo_cloned")
        result = evaluate_acceptance_criteria(job_id)
        assert result["2_stack_detected"] == "fail"

    def test_fail_when_checkpoint_blocked(self, job_id):
        self._set_blocked(job_id, "build_done")
        result = evaluate_acceptance_criteria(job_id)
        assert result["4_build_executed"] == "fail"

    def test_documents_criterion_fails_when_no_docs(self, job_id):
        result = evaluate_acceptance_criteria(job_id)
        assert result["13_documents_saved"] == "fail"

    def test_documents_criterion_passes_with_doc(self, job_id):
        store.save_document(job_id, "user_guide", "/path.pdf")
        result = evaluate_acceptance_criteria(job_id)
        assert result["13_documents_saved"] == "pass"

    def test_returns_dict_with_all_13_criteria(self, job_id):
        result = evaluate_acceptance_criteria(job_id)
        assert len(result) == 13

    def test_not_applicable_treated_as_partial(self, job_id):
        store.set_checkpoint(job_id, "gui_inspected", "not_applicable")
        result = evaluate_acceptance_criteria(job_id)
        # not_applicable is neither "done" nor "blocked"/"missing" → partial
        assert result["7_gui_inspected"] == "partial"
