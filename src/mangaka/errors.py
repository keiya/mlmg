"""Domain error type for the mangaka pipeline.

Every layer that performs external I/O (LLM, image API, file system) returns
`Result[T, MangaError]`. Bare exceptions are reserved for invariant violations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class ErrorKind(Enum):
    """High-level classification for errors across the pipeline."""

    # LLM
    LLM_CALL_FAILED = auto()       # transient — retried
    LLM_BAD_REQUEST = auto()       # 4xx (bad model / params / auth) — NOT retried
    LLM_RATE_LIMITED = auto()      # 429 — retried with backoff
    LLM_CONTEXT_TOO_LONG = auto()

    # Image generation
    IMAGE_CALL_FAILED = auto()
    IMAGE_RATE_LIMITED = auto()
    PROMPT_TOO_LONG = auto()
    REF_BUDGET_EXCEEDED = auto()

    # Parsing
    PARSE_ERROR = auto()
    JSON_INVALID = auto()
    MARKDOWN_MALFORMED = auto()
    FRONTMATTER_INVALID = auto()

    # State / pipeline
    INVALID_STATE = auto()
    MISSING_PREREQUISITE = auto()
    VALIDATION_FAILED = auto()

    # I/O & config
    IO_ERROR = auto()
    CONFIG_ERROR = auto()


@dataclass(frozen=True)
class MangaError:
    """Structured error used with `Result[T, MangaError]`.

    `kind` provides high-level classification, `message` a human-readable
    description, and `detail` optional structured diagnostics.
    """

    kind: ErrorKind
    message: str
    detail: dict[str, object] | None = None


__all__ = ["ErrorKind", "MangaError"]
