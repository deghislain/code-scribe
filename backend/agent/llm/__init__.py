"""
LLM sub-package — provider factory and module-level convenience functions.

All existing call sites use ``from backend.agent import llm; llm.ask(...)``
which imports this package as a module.  The module-level ``ask`` and
``ask_structured`` functions delegate to the active provider so call sites
need no changes.

Configure the provider via the ``LLM_PROVIDER`` environment variable
(default: ``"groq"``).  Currently only ``"groq"`` is supported; the setting
exists so alternative providers can be added without touching call sites.
"""

from __future__ import annotations

from backend.agent.llm.base import LLMProvider
from backend.agent.llm.groq_provider import GroqProvider

__all__ = ["LLMProvider", "ask", "ask_structured"]

# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def _build_provider() -> LLMProvider:
    from backend.config import settings
    provider_name = getattr(settings, "LLM_PROVIDER", "groq").lower()
    if provider_name == "groq":
        return GroqProvider()
    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider_name}'. "
        "Supported values: 'groq'."
    )


# Module-level singleton — created lazily on first call so tests can monkeypatch
# settings before the provider is instantiated.
_provider: LLMProvider | None = None


def _get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = _build_provider()
    return _provider


# ---------------------------------------------------------------------------
# Public API — mirrors the old llm.py module-level functions exactly
# ---------------------------------------------------------------------------

def ask(
    prompt: str,
    system: str = "",
    max_tokens: int = 4096,
) -> str:
    """Send a prompt to the configured LLM and return the response text."""
    return _get_provider().ask(prompt, system=system, max_tokens=max_tokens)


def ask_structured(
    prompt: str,
    system: str = "",
    schema_hint: str = "",
    max_tokens: int = 4096,
) -> str:
    """
    Ask the LLM to produce a JSON response matching *schema_hint*.
    Returns the raw response string — caller is responsible for parsing JSON.

    Implemented here (rather than delegating to the provider) so that tests
    can monkeypatch ``llm_mod.ask`` and have ``ask_structured`` pick it up.
    """
    full_prompt = prompt
    if schema_hint:
        full_prompt = (
            f"{prompt}\n\n"
            f"Respond with valid JSON matching this schema:\n{schema_hint}\n"
            "Do not include any text outside the JSON object."
        )
    return ask(full_prompt, system=system, max_tokens=max_tokens)
