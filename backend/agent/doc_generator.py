"""LLM-driven documentation generation for User Guide and Developer Guide."""

from __future__ import annotations

import json
from pathlib import Path

from backend.agent import llm
from backend.logger import get_logger
from backend.memory import store

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_safe(path: Path, max_chars: int = 2000) -> str:
    try:
        return path.read_text(errors="replace")[:max_chars]
    except OSError:
        return ""


def _tree_listing(root: Path, max_files: int = 100) -> str:
    lines: list[str] = []
    for i, p in enumerate(sorted(root.rglob("*"))):
        if i >= max_files:
            lines.append("... (truncated)")
            break
        parts = p.relative_to(root).parts
        if any(part.startswith(".") or part in {"__pycache__", "node_modules", ".git"} for part in parts):
            continue
        lines.append(str(p.relative_to(root)))
    return "\n".join(lines)


def _section(title: str, body: str) -> str:
    return f"<section>\n<h2>{title}</h2>\n{body}\n</section>\n"


def _llm_section(
    section_title: str,
    evidence: str,
    instruction: str,
    max_tokens: int = 2048,
) -> str:
    """Ask LLM to write one documentation section, return HTML."""
    prompt = (
        f"You are a technical writer generating the '{section_title}' section "
        f"of a software documentation document.\n\n"
        f"Evidence from the repository:\n{evidence}\n\n"
        f"Instructions: {instruction}\n\n"
        "Write the section content as clean HTML paragraphs, lists, and code blocks. "
        "If you cannot confirm a claim from the evidence, prepend the sentence with [UNVERIFIED]. "
        "Do not include an <h2> heading — it will be added automatically."
    )
    return llm.ask(prompt, max_tokens=max_tokens)


def _run_sections_sequential(tasks: list[tuple]) -> dict[int, str]:
    """
    Execute LLM section tasks one at a time to avoid exceeding the LLM
    token-per-minute rate limit.

    *tasks* is a list of (index, title, evidence, instruction, max_tokens).
    Returns a dict mapping index → rendered HTML string for that section body.
    """
    results: dict[int, str] = {}
    log.debug("Generating %d LLM sections sequentially", len(tasks))
    for idx, title, evidence, instruction, max_tokens in tasks:
        log.debug("Generating section: %s", title)
        results[idx] = _llm_section(title, evidence, instruction, max_tokens)
    log.debug("All %d sections generated", len(tasks))
    return results


# ---------------------------------------------------------------------------
# User Guide
# ---------------------------------------------------------------------------

def generate_user_guide(
    job_id: int,
    stack_profile: dict,
    ui_map: dict,
    checkpoints: list[dict],
    repo_dir: str,
) -> str:
    """
    Generate the End User Guide as a complete HTML string.
    All LLM section calls are issued concurrently.
    Returns HTML ready to be wrapped and rendered to PDF.
    """
    root = Path(repo_dir)
    readme = _read_safe(root / "README.md") or _read_safe(root / "README.rst")
    tree = _tree_listing(root)
    frameworks = ", ".join(stack_profile.get("frameworks", ["unknown"]))
    languages = ", ".join(stack_profile.get("languages", ["unknown"]))
    routes_text = "\n".join(
        f"  {r['path']}: {r.get('description', '')}"
        for r in ui_map.get("routes", [])
    )
    test_checkpoint = next(
        (c for c in checkpoints if c["checkpoint_name"] == "tests_run"), {}
    )
    test_evidence = json.dumps(test_checkpoint.get("evidence", {}), indent=2)[:1000]

    ui_section_body = (
        "<p>[UNVERIFIED] No GUI routes were detected for this project.</p>"
        if not ui_map.get("routes")
        else None  # will be filled by parallel call below
    )

    # Build task list for all LLM sections.
    # Each tuple: (section_index, title, evidence, instruction, max_tokens)
    # Section indices match their final insertion order (0-based within LLM sections).
    tasks: list[tuple] = [
        (0, "System Requirements",
         f"Stack: {frameworks}\nLanguages: {languages}\n\nREADME:\n{readme}",
         "List minimum OS, runtime versions, and browser requirements for an end user.",
         1024),
        (1, "Installation",
         f"README:\n{readme}\n\nDirectory tree:\n{tree}",
         "Provide step-by-step installation instructions for an end user. Focus on what "
         "a non-developer needs to do to get the application running.",
         1024),
        (2, "Getting Started",
         f"README:\n{readme}\nRoutes:\n{routes_text}",
         "Describe how to launch the application and reach the main entry point.",
         1024),
        (4, "Core Features",
         f"README:\n{readme}\nRoutes:\n{routes_text}",
         "List and describe the main features available to end users.",
         1024),
        (5, "Configuration",
         f"README:\n{readme}\nDirectory tree:\n{tree}",
         "Describe any end-user-facing configuration options (settings files, environment variables visible to users, etc.).",
         1024),
        (6, "Common Tasks",
         f"README:\n{readme}\nRoutes:\n{routes_text}",
         "Describe 3–5 common tasks an end user would perform. Use numbered steps.",
         1024),
        (7, "Troubleshooting",
         f"README:\n{readme}\nTest output:\n{test_evidence}",
         "List common user-facing problems and their solutions.",
         1024),
        (8, "Frequently Asked Questions",
         f"README:\n{readme}\nRoutes:\n{routes_text}",
         "Write 4–6 FAQ entries in a Q&A format relevant to end users.",
         1024),
        (9, "Glossary",
         f"Stack: {frameworks} / {languages}\nREADME:\n{readme}",
         "Define 5–8 technical terms an end user may encounter.",
         1024),
        (10, "Support and Feedback",
         f"README:\n{readme}",
         "Describe how end users can get help or report issues.",
         512),
        (11, "Appendices",
         f"Stack: {frameworks}\nRoutes:\n{routes_text}",
         "Include any keyboard shortcuts, accessibility notes, or additional reference material.",
         512),
    ]

    # Add UI overview section to the parallel batch if there are routes
    if ui_map.get("routes"):
        tasks.append((3, "User Interface Overview",
                       f"Routes and pages:\n{routes_text}",
                       "Describe each major page/view in plain language. What does the user see and do there?",
                       2048))

    log.info("Generating End User Guide for job %d (%d sections)", job_id, len(tasks) + 1)
    llm_results = _run_sections_sequential(tasks)

    # Assemble sections in order
    sections_html = ""

    # Section 1: Static overview
    sections_html += _section(
        "Overview",
        f"<p>This guide covers the end-user functionality of the application analyzed "
        f"from the repository. Stack: <strong>{frameworks}</strong> ({languages}).</p>"
        f"<p>{readme[:800]}</p>",
    )

    sections_html += _section("System Requirements",       llm_results[0])
    sections_html += _section("Installation",              llm_results[1])
    sections_html += _section("Getting Started",           llm_results[2])
    sections_html += _section(
        "User Interface Overview",
        llm_results.get(3, ui_section_body),
    )
    sections_html += _section("Core Features",             llm_results[4])
    sections_html += _section("Configuration",             llm_results[5])
    sections_html += _section("Common Tasks",              llm_results[6])
    sections_html += _section("Troubleshooting",           llm_results[7])
    sections_html += _section("Frequently Asked Questions",llm_results[8])
    sections_html += _section("Glossary",                  llm_results[9])
    sections_html += _section("Support and Feedback",      llm_results[10])
    sections_html += _section("Appendices",                llm_results[11])

    return sections_html


# ---------------------------------------------------------------------------
# Developer / Code Guide
# ---------------------------------------------------------------------------

def generate_dev_guide(
    job_id: int,
    stack_profile: dict,
    checkpoints: list[dict],
    repo_dir: str,
) -> str:
    """
    Generate the Developer / Code Guide as a complete HTML string.
    All LLM section calls are issued concurrently.
    """
    root = Path(repo_dir)
    readme = _read_safe(root / "README.md") or _read_safe(root / "README.rst")
    tree = _tree_listing(root)
    languages = ", ".join(stack_profile.get("languages", ["unknown"]))
    frameworks = ", ".join(stack_profile.get("frameworks", ["unknown"]))
    build_system = stack_profile.get("build_system", "unknown")
    test_frameworks = ", ".join(stack_profile.get("test_frameworks", ["unknown"]))

    build_cp = next((c for c in checkpoints if c["checkpoint_name"] == "build_done"), {})
    test_cp = next((c for c in checkpoints if c["checkpoint_name"] == "tests_run"), {})
    build_evidence = json.dumps(build_cp.get("evidence", {}), indent=2)[:800]
    test_evidence = json.dumps(test_cp.get("evidence", {}), indent=2)[:800]

    # Read a handful of key source files for context
    source_snippets: list[str] = []
    for ext in ["*.py", "*.js", "*.ts", "*.java", "*.go", "*.rs"]:
        for f in sorted(root.rglob(ext))[:4]:
            if any(skip in str(f) for skip in ["node_modules", "__pycache__", ".git"]):
                continue
            try:
                source_snippets.append(
                    f"=== {f.relative_to(root)} ===\n{f.read_text(errors='replace')[:600]}"
                )
            except OSError:
                pass
    source_context = "\n\n".join(source_snippets[:6])
    req_text = _read_safe(root / "requirements.txt") + _read_safe(root / "pyproject.toml")[:1000]
    changelog = (
        _read_safe(root / "CHANGELOG.md")
        or _read_safe(root / "CHANGELOG")
        or "[UNVERIFIED] No CHANGELOG found."
    )

    # Build task list — (index, title, evidence, instruction, max_tokens)
    tasks: list[tuple] = [
        (0, "Architecture Overview",
         f"Directory tree:\n{tree}\n\nSource snippets:\n{source_context}",
         "Describe the high-level architecture: layers, modules, data flow. Keep it concise.",
         1024),
        (1, "Repository Structure",
         f"Directory tree:\n{tree}",
         "Briefly explain the purpose of each top-level directory and key files.",
         1024),
        (2, "Setup and Environment",
         f"README:\n{readme}\nDirectory tree:\n{tree}",
         "Provide developer setup instructions: clone, install deps, configure env vars, run locally.",
         1024),
        (3, "Dependencies",
         f"Dependency files:\n{req_text[:2000]}",
         "List and briefly describe key dependencies and why they are used.",
         1024),
        (4, "Core Logic and Key Modules",
         f"Source snippets:\n{source_context}",
         "Explain the core business logic: what the main modules do, key algorithms or patterns.",
         1024),
        (5, "API Reference",
         f"Source snippets:\n{source_context}\nDirectory tree:\n{tree}",
         "Document the public API: endpoints, functions, or classes a developer would call. "
         "Use a definition list or table format.",
         1024),
        (6, "Data Models",
         f"Source snippets:\n{source_context}",
         "Describe key data structures, database schemas, or type definitions.",
         1024),
        (7, "Configuration Reference",
         f"README:\n{readme}\nDirectory tree:\n{tree}",
         "Document all configuration parameters: env vars, config files, defaults, and valid values.",
         1024),
        (8, "Build and Deployment",
         f"Build system: {build_system}\nBuild output:\n{build_evidence}\nREADME:\n{readme}",
         "Document how to build, package, and deploy the application.",
         1024),
        (9, "Testing",
         f"Test frameworks: {test_frameworks}\nTest output:\n{test_evidence}",
         "Explain how to run the test suite, interpret results, and add new tests.",
         1024),
        (10, "Contributing",
         f"README:\n{readme}",
         "Describe the contribution workflow: branching, PR process, code style, commit conventions.",
         1024),
        (11, "Troubleshooting and Known Issues",
         f"Build output:\n{build_evidence}\nTest output:\n{test_evidence}",
         "List common developer problems, error messages, and their solutions.",
         1024),
    ]

    log.info("Generating Developer Guide for job %d (%d sections)", job_id, len(tasks) + 1)
    llm_results = _run_sections_sequential(tasks)

    sections_html = ""

    # Section 1: Static project overview
    sections_html += _section(
        "Project Overview",
        f"<p>Language(s): <strong>{languages}</strong> — "
        f"Frameworks: <strong>{frameworks}</strong> — "
        f"Build system: <strong>{build_system}</strong></p>"
        f"<p>{readme[:600]}</p>",
    )

    sections_html += _section("Architecture Overview",          llm_results[0])
    sections_html += _section(
        "Repository Structure",
        f"<pre><code>{tree}</code></pre>\n" + llm_results[1],
    )
    sections_html += _section("Setup and Environment",          llm_results[2])
    sections_html += _section("Dependencies",                   llm_results[3])
    sections_html += _section("Core Logic and Key Modules",     llm_results[4])
    sections_html += _section("API Reference",                  llm_results[5])
    sections_html += _section("Data Models",                    llm_results[6])
    sections_html += _section("Configuration Reference",        llm_results[7])
    sections_html += _section("Build and Deployment",           llm_results[8])
    sections_html += _section("Testing",                        llm_results[9])
    sections_html += _section("Contributing",                   llm_results[10])
    sections_html += _section("Troubleshooting and Known Issues", llm_results[11])

    # Section 14: Static changelog
    sections_html += _section(
        "Changelog",
        f"<pre>{changelog[:2000]}</pre>",
    )

    return sections_html
