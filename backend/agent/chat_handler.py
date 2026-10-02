"""Chat handler: answer user questions grounded in repository evidence."""

from __future__ import annotations

from pathlib import Path

from backend.agent import llm, web_search
from backend.agent.knowledge_matcher import find_relevant
from backend.config import settings
from backend.memory import store

_MAX_FILE_CHARS = 3000   # cap per file to keep prompt size manageable
_MAX_SOURCE_FILES = 4    # number of source files to include


def _read_safe(path: Path, max_chars: int = _MAX_FILE_CHARS) -> str:
    try:
        return path.read_text(errors="replace")[:max_chars]
    except OSError:
        return ""


def _build_repo_context(job_id: int) -> str:
    """
    Gather real repository content (README, deps, key source files) so the LLM
    has concrete evidence to answer questions about the project.
    """
    job = store.get_job(job_id)
    if not job:
        return ""

    # Derive repo directory from job URL (same logic as orchestrator)
    repo_name = job["repo_url"].rstrip("/").split("/")[-1].replace(".git", "")
    repo_dir = Path(settings.WORKSPACE_DIR) / repo_name
    if not repo_dir.exists():
        return ""

    parts: list[str] = []

    # README
    for name in ("README.md", "README.rst", "README.txt", "README"):
        text = _read_safe(repo_dir / name)
        if text:
            parts.append(f"=== {name} ===\n{text}")
            break

    # Dependency / lock files
    for name in ("requirements.txt", "pyproject.toml", "package.json",
                 "Pipfile", "go.mod", "Cargo.toml", "pom.xml", "build.gradle"):
        text = _read_safe(repo_dir / name, max_chars=2000)
        if text:
            parts.append(f"=== {name} ===\n{text}")

    # Key source files
    source_count = 0
    for ext in ("*.py", "*.js", "*.ts", "*.go", "*.rs", "*.java"):
        for f in sorted(repo_dir.rglob(ext)):
            if source_count >= _MAX_SOURCE_FILES:
                break
            if any(skip in str(f) for skip in ("node_modules", "__pycache__", ".git", "test")):
                continue
            text = _read_safe(f, max_chars=1500)
            if text:
                parts.append(f"=== {f.relative_to(repo_dir)} ===\n{text}")
                source_count += 1

    return "\n\n".join(parts)


_UNVERIFIABLE = "i cannot verify this"


def _is_unverifiable(text: str) -> bool:
    return _UNVERIFIABLE in text.lower()


def _web_search_answer(question: str) -> str:
    """
    Search the web for *question*, fetch the top pages, and ask the LLM to
    synthesise a cited answer from those results.
    Returns the LLM response string.
    """
    results = web_search.search_and_fetch(question, max_results=3)
    if not results:
        return "I could not find relevant information on the web to answer this question."

    # Build a context block with title, URL, and fetched page text
    web_parts: list[str] = []
    for r in results:
        body = r.get("page_text") or r.get("snippet") or ""
        web_parts.append(
            f"Source: {r['title']}\nURL: {r['url']}\n{body}"
        )
    web_context = "\n\n---\n\n".join(web_parts)

    system = (
        "You are Code-Scribe, a helpful technical assistant. "
        "Answer the user's question using ONLY the web sources provided below. "
        "At the end of your answer include a 'Sources:' section listing each URL you used. "
        "If the sources do not contain enough information, say so clearly."
    )
    prompt = (
        f"Web sources:\n\n{web_context}\n\n"
        f"Question: {question}"
    )
    return llm.ask(prompt, system=system, max_tokens=1024)


def answer_question(job_id: int, question: str) -> str:
    """
    Answer a user question grounded in the job's repository content and learned knowledge.
    If the repo context is insufficient, falls back to a live web search.

    Strategy:
    1. Pull real repo content (README, deps, source files) as primary evidence.
    2. Prepend top-5 relevant prior Q&A pairs from learned knowledge.
    3. Ask the LLM — if it answers from the repo, store and return.
    4. If the LLM says it cannot verify: run a web search, synthesise a cited
       answer from live results, store and return.
    """
    knowledge = store.get_learned_knowledge(job_id)
    relevant = find_relevant(question, knowledge, top_k=5)

    # Build context from prior Q&A pairs
    context_parts: list[str] = []
    for item in relevant:
        context_parts.append(f"Q: {item['question']}\nA: {item['answer']}")
    knowledge_context = "\n\n".join(context_parts)

    # Real repository content
    repo_context = _build_repo_context(job_id)

    # Checkpoint summary (for pipeline awareness)
    checkpoints = store.get_checkpoints(job_id)
    cp_summary = "\n".join(
        f"  [{cp['checkpoint_name']}] {cp['status']}"
        for cp in checkpoints
    )

    system = (
        "You are Code-Scribe, a helpful assistant with expert knowledge of a specific "
        "software repository that was analyzed. Answer questions based only on the "
        "information provided in the context below. If you cannot verify an answer from the "
        "context, say exactly: 'I cannot verify this from the codebase.' "
        "Never speculate or make up file names, functions, or behaviors."
    )

    prompt = (
        (f"Repository files:\n{repo_context}\n\n" if repo_context else "")
        + f"Pipeline summary:\n{cp_summary}\n\n"
        + (f"Relevant prior knowledge:\n{knowledge_context}\n\n" if knowledge_context else "")
        + f"User question: {question}"
    )

    response = llm.ask(prompt, system=system, max_tokens=1024)

    # If the repo context wasn't enough, search the web instead
    if _is_unverifiable(response):
        response = _web_search_answer(question)

    # Determine if grounded: must NOT contain the "cannot verify" phrase
    is_grounded = not _is_unverifiable(response)

    if is_grounded:
        store.save_learned_knowledge(
            job_id=job_id,
            question=question,
            answer=response,
            evidence=cp_summary[:500],
            confidence=0.9,
        )
        store.save_user_interaction(
            job_id=job_id,
            user_message=question,
            agent_response=response,
            validated=True,
        )
    else:
        store.save_user_interaction(
            job_id=job_id,
            user_message=question,
            agent_response=response,
            validated=False,
        )

    return response
