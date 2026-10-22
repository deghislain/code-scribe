"""LLM provider protocol — defines the interface all providers must implement."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    """Minimal interface every LLM provider must satisfy."""

    def ask(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
    ) -> str:
        """Send a prompt and return the response text."""
        ...

    def ask_structured(
        self,
        prompt: str,
        system: str = "",
        schema_hint: str = "",
        max_tokens: int = 4096,
    ) -> str:
        """Ask the LLM to produce a JSON response matching *schema_hint*."""
        ...
