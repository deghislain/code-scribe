"""Groq LLM provider implementation."""

from __future__ import annotations

import threading
import time
import traceback

from groq import Groq, RateLimitError, APIStatusError

from backend.config import settings
from backend.logger import get_logger

log = get_logger(__name__)


class GroqProvider:
    """
    Groq-backed LLM provider.

    Uses a module-level semaphore to cap simultaneous in-flight requests and
    avoid blowing the TPM rate limit on the free tier (8K TPM).
    """

    # Shared across all GroqProvider instances in the process
    _REQUEST_SEMAPHORE = threading.Semaphore(3)

    def __init__(self) -> None:
        self._client: Groq | None = None

    def _get_client(self) -> Groq:
        if self._client is None:
            self._client = Groq(api_key=settings.GROQ_API_KEY)
        return self._client

    def ask(
        self,
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
            with self._REQUEST_SEMAPHORE:
                try:
                    log.debug(
                        "Groq request — model=%s max_tokens=%d attempt=%d",
                        settings.GROQ_MODEL,
                        max_tokens,
                        attempt + 1,
                    )
                    response = self._get_client().chat.completions.create(
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
            wait = 2 ** attempt * 5
            time.sleep(wait)

        raise RuntimeError(
            f"Groq API failed after 3 retries: {last_exc}"
        ) from last_exc

    def ask_structured(
        self,
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
        return self.ask(full_prompt, system=system, max_tokens=max_tokens)
