"""LLM client protocol, a real proxy implementation, and a deterministic fake."""

import json
import os
import time
from typing import Any, Protocol


class LLMClient(Protocol):
    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0
    ) -> str: ...


class FakeLLM:
    """Deterministic LLM stub: returns scripted replies in order, records calls."""

    def __init__(self, scripted: list[str]) -> None:
        self._scripted = list(scripted)
        self._i = 0
        self._calls: list[dict[str, Any]] = []

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0
    ) -> str:
        self._calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens, "temperature": temperature}
        )
        reply = self._scripted[self._i]  # IndexError when the script is exhausted
        self._i += 1
        return reply

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._calls


class ProxyLLM:
    """Anthropic-messages client pointed at the local proxy via env vars.

    Reads ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN (Bearer). ``anthropic`` is
    imported lazily so the core package does not require it.
    """

    def __init__(self, model: str = "claude-opus-4-8") -> None:
        import anthropic

        self._client = anthropic.Anthropic(
            base_url=os.environ.get("ANTHROPIC_BASE_URL"),
            auth_token=os.environ.get("ANTHROPIC_AUTH_TOKEN"),
        )
        self._model = model

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0
    ) -> str:
        last_exc: Exception | None = None
        for attempt in range(3):  # transient proxy/network errors -> back off and retry
            try:
                message = self._client.messages.create(
                    model=self._model,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return "".join(
                    getattr(block, "text", "")
                    for block in message.content
                    if getattr(block, "type", None) == "text"
                )
            except Exception as exc:  # noqa: BLE001 - transient; retried then re-raised
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"LLM call failed after retries: {last_exc}") from last_exc


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first balanced JSON object from ``text`` (tolerates ```json
    fences and surrounding prose). Raises ValueError if none parses."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break  # not valid; try the next '{'
                    if isinstance(obj, dict):
                        return obj
                    break
        start = text.find("{", start + 1)
    raise ValueError(f"no JSON object found in: {text[:80]!r}")
