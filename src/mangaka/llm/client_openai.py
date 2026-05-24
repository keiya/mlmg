"""OpenAI `LLMClient` backed by the Responses API.

Anthropic-style `thinking_budget` (token count) is replaced by Responses'
`reasoning.effort` (`minimal` / `low` / `medium` / `high`). When `thinking=True`
we pass the configured effort verbatim; the config validator enforces that
the effort is always set in that case.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    OpenAI,
    RateLimitError,
)

from mangaka.config import LayerConfig, RetryConfig
from mangaka.errors import ErrorKind, MangaError
from mangaka.llm.client import ReasoningEffort
from mangaka.llm.retry import RetryHandler
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

if TYPE_CHECKING:
    from openai.types.responses import Response

logger = get_logger(__name__)


@dataclass
class OpenAILLMClient:
    """Production `LLMClient` using OpenAI Responses API.

    `default_model` is a fallback only — callers normally pass `model=` explicit.
    `retry_config` wraps every call; transient `LLM_RATE_LIMITED` /
    `LLM_CALL_FAILED` errors backoff exponentially.
    """

    default_model: str
    retry_config: RetryConfig
    api_key: str | None = None
    _client: OpenAI | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        key = self.api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            logger.warning("openai_api_key_missing")
            self._client = None
        else:
            # `max_retries=0` disables the SDK's own retry layer — we have
            # `RetryHandler` wrapping every call, and stacking two retry
            # policies would multiply attempts (up to 12 LLM calls with
            # default config) and silently blow through the documented
            # `retry.max_retries` budget.
            self._client = OpenAI(api_key=key, max_retries=0)

    def _make_request(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        thinking: bool,
        reasoning_effort: ReasoningEffort | None,
    ) -> Result[str, MangaError]:
        if self._client is None:
            return Failure(
                MangaError(
                    kind=ErrorKind.CONFIG_ERROR,
                    message=(
                        "OpenAI client not initialized — set OPENAI_API_KEY "
                        "in .env or pass api_key= to OpenAILLMClient"
                    ),
                )
            )
        try:
            kwargs: dict[str, object] = {
                "model": model,
                "input": prompt,
                "max_output_tokens": max_tokens,
            }
            # Reasoning models reject explicit temperature; only set when not thinking.
            if not thinking:
                kwargs["temperature"] = temperature
            if thinking and reasoning_effort is not None:
                kwargs["reasoning"] = {"effort": reasoning_effort}

            # `responses.create` returns `Response | Stream` in general; we never
            # pass stream=True so the cast is safe.
            response = cast(
                "Response",
                self._client.responses.create(**kwargs),  # type: ignore[arg-type]
            )
        except RateLimitError as exc:
            return Failure(
                MangaError(
                    kind=ErrorKind.LLM_RATE_LIMITED,
                    message=f"OpenAI rate limit: {exc}",
                )
            )
        except APIStatusError as exc:
            status_code = getattr(exc, "status_code", None)
            # 4xx (except 429, which RateLimitError covered above) are permanent —
            # invalid API key, invalid model, bad params, context too long.
            # Retrying these just wastes backoff time and money.
            if isinstance(status_code, int) and 400 <= status_code < 500:
                kind = ErrorKind.LLM_BAD_REQUEST
            else:
                kind = ErrorKind.LLM_CALL_FAILED
            return Failure(
                MangaError(
                    kind=kind,
                    message=f"OpenAI API error: {exc}",
                    detail={"status_code": status_code},
                )
            )
        except APIConnectionError as exc:
            return Failure(
                MangaError(
                    kind=ErrorKind.LLM_CALL_FAILED,
                    message=f"OpenAI connection error: {exc}",
                )
            )

        # Reject incomplete responses BEFORE returning the partial text —
        # otherwise truncated Plot/Backstory/MPBV markdown gets persisted
        # as canonical layer output. `status` is "completed" on success;
        # "incomplete" / "failed" indicate max_output_tokens hit, content
        # filter, or upstream failure. Treat as a retryable call-failed
        # so the operator can re-run with a larger max_tokens budget.
        status = getattr(response, "status", None)
        if status not in (None, "completed"):
            incomplete = getattr(response, "incomplete_details", None)
            reason = getattr(incomplete, "reason", None)
            return Failure(
                MangaError(
                    kind=ErrorKind.LLM_CALL_FAILED,
                    message=(
                        f"OpenAI response did not complete (status={status!r}, "
                        f"reason={reason!r}) — partial output rejected"
                    ),
                    detail={
                        "model": model,
                        "status": status,
                        "incomplete_reason": reason,
                    },
                )
            )

        text = response.output_text or ""
        if not text:
            return Failure(
                MangaError(
                    kind=ErrorKind.LLM_CALL_FAILED,
                    message="OpenAI response contained no text output",
                    detail={"model": model},
                )
            )

        usage = response.usage
        logger.debug(
            "llm_request_completed",
            model=model,
            output_chars=len(text),
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
        )
        return Success(text)

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
        handler = RetryHandler(self.retry_config)
        return handler.execute(
            lambda: self._make_request(
                prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
            ),
            operation_name=f"llm_complete({model})",
        )

    def complete_with_layer(
        self,
        prompt: str,
        layer_config: LayerConfig,
    ) -> Result[str, MangaError]:
        """Convenience: dispatch a call using a `LayerConfig` directly."""
        return self.complete(
            prompt,
            model=layer_config.model,
            temperature=layer_config.temperature,
            max_tokens=layer_config.max_tokens,
            thinking=layer_config.thinking,
            reasoning_effort=layer_config.reasoning_effort,
        )


__all__ = ["OpenAILLMClient"]
