"""Exponential-backoff retry helper.

Retries on transient `LLM_RATE_LIMITED` / `LLM_CALL_FAILED` only. Caller
supplies a thunk returning a `Result`; this module sleeps between attempts.
Retry policy is owned by the caller (mlsg2 AGENTS.md §Agents and tool layers)
— I/O functions don't retry themselves.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from mangaka.config import RetryConfig
from mangaka.errors import ErrorKind, MangaError
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

_RETRYABLE_KINDS = frozenset(
    {
        ErrorKind.LLM_RATE_LIMITED,
        ErrorKind.LLM_CALL_FAILED,
        ErrorKind.IMAGE_RATE_LIMITED,
        ErrorKind.IMAGE_CALL_FAILED,
    }
)


@dataclass(frozen=True)
class RetryHandler:
    config: RetryConfig

    def is_retryable(self, error: MangaError) -> bool:
        return error.kind in _RETRYABLE_KINDS

    def calculate_delay(self, attempt: int) -> float:
        """Exponential backoff capped at `max_delay`, with multiplicative jitter.

        Jitter disperses retry waves when N parallel workers all hit 429 at
        the same instant. Each call samples a fresh uniform multiplier in
        `[1 - jitter_ratio, 1 + jitter_ratio]`. Sampling is per-call and not
        seeded by attempt, so two threads at the same attempt count get
        different delays.
        """
        delay = self.config.initial_delay * (self.config.exponential_base ** attempt)
        delay = min(delay, self.config.max_delay)
        if self.config.jitter_ratio > 0.0:
            jitter = random.uniform(
                1.0 - self.config.jitter_ratio,
                1.0 + self.config.jitter_ratio,
            )
            delay *= jitter
        return delay

    def execute[T](
        self,
        operation: Callable[[], Result[T, MangaError]],
        *,
        operation_name: str = "operation",
    ) -> Result[T, MangaError]:
        """Run `operation` with exponential backoff on retryable failures."""
        last_error: MangaError | None = None

        for attempt in range(self.config.max_retries + 1):
            result = operation()
            if isinstance(result, Success):
                if attempt > 0:
                    logger.info(
                        "retry_succeeded",
                        operation=operation_name,
                        attempt=attempt,
                    )
                return result

            # Failure branch
            error = result.failure()
            last_error = error

            if not self.is_retryable(error):
                logger.warning(
                    "non_retryable_error",
                    operation=operation_name,
                    error_kind=error.kind.name,
                    message=error.message,
                )
                return result

            if attempt < self.config.max_retries:
                delay = self.calculate_delay(attempt)
                logger.warning(
                    "retrying",
                    operation=operation_name,
                    attempt=attempt + 1,
                    max_retries=self.config.max_retries,
                    delay_seconds=delay,
                    error_kind=error.kind.name,
                )
                time.sleep(delay)
            else:
                logger.error(
                    "max_retries_exceeded",
                    operation=operation_name,
                    max_retries=self.config.max_retries,
                    error_kind=error.kind.name,
                    message=error.message,
                )

        # Loop only exits via `return` above on Success or non-retryable.
        # Falling out means retries were exhausted on a retryable error.
        if last_error is not None:
            return Failure(last_error)
        return Failure(
            MangaError(
                kind=ErrorKind.INVALID_STATE,
                message=f"retry loop for {operation_name} exited without a result",
            )
        )


__all__ = ["RetryHandler"]
