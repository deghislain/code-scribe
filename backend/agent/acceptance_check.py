"""Evaluate acceptance criteria from persisted checkpoints and documents."""

from __future__ import annotations

from backend.memory import store


# Mapping: acceptance criterion label → required checkpoint names (all must be "done")
_CRITERIA: dict[str, list[str]] = {
    "1_repo_cloned": ["repo_cloned"],
    "2_stack_detected": ["stack_detected"],
    "3_deps_installed": ["deps_installed"],
    "4_build_executed": ["build_done"],
    "5_tests_run": ["tests_run"],
    "6_app_launched": ["app_launched"],
    "7_gui_inspected": ["gui_inspected"],
    "8_user_guide_generated": ["user_guide_generated"],
    "9_dev_guide_generated": ["dev_guide_generated"],
    "10_chat_injected": ["chat_view_injected"],
    "11_tests_rerun": ["tests_rerun"],
    "12_memory_saved": ["memory_saved"],
    "13_documents_saved": [],  # Checked via documents table
}


def evaluate_acceptance_criteria(job_id: int) -> dict[str, str]:
    """
    Read checkpoints and documents tables; return a dict mapping each criterion
    to "pass", "fail", or "partial".
    """
    checkpoints = store.get_checkpoints(job_id)
    cp_map: dict[str, str] = {c["checkpoint_name"]: c["status"] for c in checkpoints}

    documents = store.get_documents(job_id)
    doc_types = {d["doc_type"] for d in documents}

    result: dict[str, str] = {}
    for criterion, required_cps in _CRITERIA.items():
        if criterion == "13_documents_saved":
            if doc_types:
                result[criterion] = "pass"
            else:
                result[criterion] = "fail"
            continue

        statuses = [cp_map.get(cp, "missing") for cp in required_cps]
        if all(s == "done" for s in statuses):
            result[criterion] = "pass"
        elif any(s in ("blocked", "missing") for s in statuses):
            result[criterion] = "fail"
        else:
            result[criterion] = "partial"

    return result
