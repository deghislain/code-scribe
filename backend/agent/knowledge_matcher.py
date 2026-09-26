"""Knowledge token-overlap matcher — no ML or embeddings required."""

from __future__ import annotations

import re


def _tokenize(text: str) -> set[str]:
    """Lower-case word tokenizer; returns a set of tokens."""
    return set(re.findall(r"\b\w+\b", text.lower()))


def find_relevant(
    question: str,
    knowledge: list[dict],
    top_k: int = 5,
) -> list[dict]:
    """
    Score each knowledge item by token overlap between *question* and the stored
    question text. Return the top-*k* items sorted by descending score.
    Only items with a non-zero score are returned.
    """
    q_tokens = _tokenize(question)
    if not q_tokens:
        return []

    scored: list[tuple[int, dict]] = []
    for item in knowledge:
        k_tokens = _tokenize(item.get("question", ""))
        overlap = len(q_tokens & k_tokens)
        if overlap > 0:
            scored.append((overlap, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]
