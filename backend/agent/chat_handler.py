"""Chat handler: answer user questions grounded in repository evidence."""

from __future__ import annotations

from backend.agent import llm
from backend.agent.knowledge_matcher import find_relevant
from backend.memory import store


def answer_question(job_id: int, question: str) -> str:
    """
    Answer a user question grounded in the job's documentation and learned knowledge.

    Strategy:
    1. Load learned knowledge for this job; prepend top-5 relevant Q&A pairs.
    2. Call LLM with the grounded context.
    3. If the answer is grounded (cites a route/path/feature): persist to learned_knowledge.
    4. If not verifiable: return a standard "cannot verify" reply without storing.
    """
    knowledge = store.get_learned_knowledge(job_id)
    relevant = find_relevant(question, knowledge, top_k=5)

    # Build context preamble from learned knowledge
    context_parts: list[str] = []
    for item in relevant:
        context_parts.append(
            f"Q: {item['question']}\nA: {item['answer']}"
        )
    knowledge_context = "\n\n".join(context_parts)

    # Load document summaries from checkpoints
    checkpoints = store.get_checkpoints(job_id)
    cp_summary = "\n".join(
        f"  [{cp['checkpoint_name']}] {cp['status']}"
        for cp in checkpoints
    )

    system = (
        "You are Code-Scribe, a helpful assistant with expert knowledge of a specific "
        "software repository that was analyzed. Answer questions based only on the "
        "information provided in the context. If you cannot verify an answer from the "
        "context, say exactly: 'I cannot verify this from the codebase.' "
        "Never speculate or make up file names, functions, or behaviors."
    )

    prompt = (
        f"Repository analysis summary:\n{cp_summary}\n\n"
        + (f"Relevant prior knowledge:\n{knowledge_context}\n\n" if knowledge_context else "")
        + f"User question: {question}"
    )

    response = llm.ask(prompt, system=system, max_tokens=1024)

    # Determine if grounded: must NOT contain the "cannot verify" phrase
    _UNVERIFIABLE = "i cannot verify this"
    is_grounded = _UNVERIFIABLE not in response.lower()

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
