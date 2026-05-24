"""LLM client Protocol.

Concrete implementations live in `client_openai.py` (production) and
`client_fake.py` (tests). `reasoning_effort` maps to OpenAI Responses API's
`reasoning.effort`; it is only meaningful when `thinking=True`.
"""

from __future__ import annotations

from typing import Literal, Protocol

from mangaka.errors import MangaError
from mangaka.result import Result

ReasoningEffort = Literal["minimal", "low", "medium", "high"]


class LLMClient(Protocol):
    """Pure-text completion Protocol.

    Implementations must:
      - Return `Result[str, MangaError]` — never raise for expected failures.
      - Honor `thinking` + `reasoning_effort` consistently (Responses API
        semantics; Anthropic-style `thinking_budget` is not supported in v1).
      - Be safe to call from any layer; retries belong to the caller.
    """

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        thinking: bool = False,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> Result[str, MangaError]: ...


__all__ = ["LLMClient", "ReasoningEffort"]
