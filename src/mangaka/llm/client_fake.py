"""Fake LLM client for unit tests.

`FakeLLMClient` returns canned responses without calling any external API.
Tests can inject either a fixed string, a list of responses (consumed in
order), or a callable mapping prompts to responses.

The client also records every call so tests can assert on prompt content,
model, temperature, reasoning_effort, etc.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from mangaka.errors import ErrorKind, MangaError
from mangaka.llm.client import ReasoningEffort
from mangaka.result import Failure, Result, Success


@dataclass(frozen=True)
class FakeCall:
    """Record of one `complete` invocation."""

    prompt: str
    model: str
    temperature: float
    max_tokens: int
    thinking: bool
    reasoning_effort: ReasoningEffort | None


@dataclass
class FakeLLMClient:
    """Test double for `LLMClient`.

    Pick one strategy (checked in this order):
      - `results`: list of full `Result[str, MangaError]` consumed FIFO — use
        when you need to mix Success / Failure outcomes across calls.
      - `responses`: list of plain strings consumed FIFO; each wrapped as `Success`.
      - `responder`: callable receiving the prompt → returns the response string.
      - `default_response`: returned wrapped in `Success` for every call.

    `calls` accumulates every invocation so tests can assert prompt / model /
    temperature / reasoning_effort values that were passed in.
    """

    default_response: str = "FAKE_RESPONSE"
    responses: list[str] = field(default_factory=list[str])
    results: list[Result[str, MangaError]] = field(
        default_factory=list[Result[str, MangaError]]
    )
    responder: Callable[[str], str] | None = None
    calls: list[FakeCall] = field(default_factory=list[FakeCall])

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        thinking: bool = False,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> Result[str, MangaError]:
        self.calls.append(
            FakeCall(
                prompt=prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
            )
        )

        if self.results:
            return self.results.pop(0)

        if self.responder is not None:
            return Success(self.responder(prompt))

        if self.responses:
            return Success(self.responses.pop(0))

        return Success(self.default_response)

    def with_failure(self, error: MangaError) -> FakeLLMClient:
        """Variant that returns `Failure(error)` for every call.

        Useful for retry / error-path tests.
        """
        return _AlwaysFailFakeLLMClient(error=error)


@dataclass
class _AlwaysFailFakeLLMClient(FakeLLMClient):
    error: MangaError = field(
        default_factory=lambda: MangaError(
            kind=ErrorKind.LLM_CALL_FAILED, message="forced fake failure"
        )
    )

    def complete(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 8192,
        thinking: bool = False,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> Result[str, MangaError]:
        self.calls.append(
            FakeCall(
                prompt=prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
            )
        )
        return Failure(self.error)


__all__ = ["FakeCall", "FakeLLMClient"]
