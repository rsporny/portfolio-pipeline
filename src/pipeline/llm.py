from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class TransformError(RuntimeError):
    """Raised when a model response can't be obtained, parsed, or validated.

    Carries the raw model text (when available) so the caller can persist it to
    ``_failed_raw.txt`` per SPEC.
    """

    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


def strip_fences(text: str) -> str:
    """Return the contents of the first ```json … ``` fence, or the stripped text."""
    match = _FENCE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _salvage_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON *object* from ``text`` when the model wrapped it in
    prose or appended a note despite being told to emit JSON only (a common LLM
    slip — e.g. a valid object followed by an explanatory "Note: …"). Scans each
    ``{`` and returns the first one that decodes to a dict, ignoring any trailing
    data. Returns None if there is no parseable object."""
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text, idx)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            return obj
        idx = text.find("{", idx + 1)
    return None


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = strip_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        # The model emitted a valid object with prose/extra data around it —
        # salvage the object rather than losing the whole response.
        data = _salvage_json_object(cleaned)
        if data is None:
            raise TransformError(f"Model response is not valid JSON: {exc}", raw=text) from exc
        logger.warning("Recovered a JSON object from a response with extra data around it")
    if not isinstance(data, dict):
        raise TransformError("Model response JSON is not an object", raw=text)
    return data


class LLMClient:
    """Anthropic wrapper for the two-stage transform.

    Retries API errors with exponential backoff (3 attempts by default); the
    SDK's own retries are disabled to avoid compounding them. JSON parsing
    failures are *not* retried — they surface as :class:`TransformError` so the
    caller can save the raw response and stop.
    """

    def __init__(
        self,
        model: str,
        max_tokens: int,
        *,
        api_key: str | None = None,
        client: anthropic.Anthropic | None = None,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self._sleep = sleep
        self._client = client or anthropic.Anthropic(api_key=api_key, max_retries=0)

    def complete(self, prompt: str, system: str | None = None) -> str:
        """Send one prompt and return the concatenated text output, retrying on
        transient API errors."""
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if system:
                    kwargs["system"] = system
                response = self._client.messages.create(**kwargs)
                return "".join(
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                )
            except anthropic.APIError as exc:
                last_exc = exc
                if attempt < self.max_attempts:
                    delay = self.base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Anthropic API error (attempt %d/%d): %s; retrying in %.1fs",
                        attempt,
                        self.max_attempts,
                        exc,
                        delay,
                    )
                    self._sleep(delay)
        raise TransformError(f"Anthropic API failed after {self.max_attempts} attempts: {last_exc}")

    def complete_json(self, prompt: str, system: str | None = None) -> tuple[dict[str, Any], str]:
        """Return ``(parsed_dict, raw_text)``; raises TransformError on bad JSON."""
        raw = self.complete(prompt, system)
        return parse_json_response(raw), raw
