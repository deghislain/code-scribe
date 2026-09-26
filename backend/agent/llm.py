"""Groq LLM client wrapper — single entry point for all agent LLM calls."""

import threading
import time
import traceback

from groq import Groq, RateLimitError, APIStatusError

from backend.config import settings
from backend.logger import get_logger

log = get_logger(__name__)

_client: Groq | None = None

# Global semaphore shared by all callers — caps simultaneous in-flight Groq
# requests to avoid blowing the TPM rate limit on the free tier (8K TPM).
# Each section requests up to 1024 tokens; 3 concurrent ≈ 3K in-flight.
_REQUEST_SEMAPHORE = threading.Semaphore(3)


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=settings.GROQ_API_KEY)
    return _client


def ask(
    prompt: str,
    system: str = "",
    max_tokens: int = 4096,
) -> str:
    """
    Send a prompt to the configured Groq model and return the response text.

    Retries up to 3 times with exponential back-off on rate-limit / transient errors.
    """
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_exc: Exception | None = None
    for attempt in range(3):
        # Acquire the global semaphore before each attempt so that at most
        # _REQUEST_SEMAPHORE.count requests are in-flight at once across all
        # threads, keeping token throughput within the Groq TPM limit.
        with _REQUEST_SEMAPHORE:
            try:
                log.debug(
                    "Groq request — model=%s max_tokens=%d attempt=%d",
                    settings.GROQ_MODEL,
                    max_tokens,
                    attempt + 1,
                )
                response = _get_client().chat.completions.create(
                    model=settings.GROQ_MODEL,
                    messages=messages,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""
            except RateLimitError as exc:
                wait = 2 ** attempt * 5  # 5 s, 10 s, 20 s
                log.warning(
                    "Groq rate-limit hit (attempt %d/3) — backing off %ds: %s",
                    attempt + 1,
                    wait,
                    exc,
                )
                last_exc = exc
            except APIStatusError as exc:
                if exc.status_code >= 500:
                    log.warning(
                        "Groq server error %d (attempt %d/3): %s",
                        exc.status_code,
                        attempt + 1,
                        exc,
                    )
                    last_exc = exc
                else:
                    raise RuntimeError(
                        f"Groq API error {exc.status_code}: {exc.message}"
                    ) from exc
            except Exception as exc:
                raise RuntimeError(
                    f"Unexpected Groq error: {exc}\n{traceback.format_exc()}"
                ) from exc
        # Back-off outside the semaphore so other threads can proceed
        wait = 2 ** attempt * 5  # 5 s, 10 s, 20 s
        time.sleep(wait)

    raise RuntimeError(
        f"Groq API failed after 3 retries: {last_exc}"
    ) from last_exc


def ask_structured(
    prompt: str,
    system: str = "",
    schema_hint: str = "",
    max_tokens: int = 4096,
) -> str:
    """
    Ask the LLM to produce a JSON response matching *schema_hint*.
    Returns the raw response string — caller is responsible for parsing JSON.
    """
    full_prompt = prompt
    if schema_hint:
        full_prompt = (
            f"{prompt}\n\n"
            f"Respond with valid JSON matching this schema:\n{schema_hint}\n"
            "Do not include any text outside the JSON object."
        )
    return ask(full_prompt, system=system, max_tokens=max_tokens)
