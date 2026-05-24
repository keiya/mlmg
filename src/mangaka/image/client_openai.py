"""OpenAI `ImageClient` for gpt-image-2.

Uses `/v1/images/generations` for fresh text-only generation and
`/v1/images/edits` for ref-conditioned generation. Responses come back as
`b64_json`; we decode to raw PNG bytes and return them. Persisting bytes to
disk + versioned naming is the caller's job (see `layers/*.py`).
"""

from __future__ import annotations

import base64
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from openai import (
    APIConnectionError,
    APIStatusError,
    OpenAI,
    RateLimitError,
)

from mangaka.config import RetryConfig
from mangaka.errors import ErrorKind, MangaError
from mangaka.llm.retry import RetryHandler
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

if TYPE_CHECKING:
    from openai.types.images_response import ImagesResponse


logger = get_logger(__name__)


@dataclass
class OpenAIImageClient:
    """Production `ImageClient` calling gpt-image-2 via the OpenAI Images API."""

    retry_config: RetryConfig
    api_key: str | None = None
    _client: OpenAI | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        key = self.api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            logger.warning("openai_api_key_missing", context="image_client")
            self._client = None
        else:
            # See OpenAILLMClient: `max_retries=0` disables SDK-internal retries so
            # `RetryHandler` is the only retry layer (image calls are expensive —
            # stacking retries would blow through `limits.max_image_retries`).
            self._client = OpenAI(api_key=key, max_retries=0)

    def _classify_status_error(self, exc: APIStatusError) -> MangaError:
        """Same 4xx/5xx split as `OpenAILLMClient`, using image error kinds."""
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int) and 400 <= status_code < 500:
            # Bad model / params / auth: reuse LLM_BAD_REQUEST since it is
            # already non-retryable. The image-specific equivalent isn't worth
            # a new enum variant for v1.
            kind = ErrorKind.LLM_BAD_REQUEST
        else:
            kind = ErrorKind.IMAGE_CALL_FAILED
        return MangaError(
            kind=kind,
            message=f"OpenAI image API error: {exc}",
            detail={"status_code": status_code},
        )

    def _extract_bytes(self, response: ImagesResponse) -> Result[bytes, MangaError]:
        data = response.data
        if not data:
            return Failure(
                MangaError(
                    kind=ErrorKind.IMAGE_CALL_FAILED,
                    message="OpenAI image response contained no data",
                )
            )
        first = data[0]
        b64 = first.b64_json
        if not b64:
            return Failure(
                MangaError(
                    kind=ErrorKind.IMAGE_CALL_FAILED,
                    message="OpenAI image response missing b64_json",
                )
            )
        try:
            return Success(base64.b64decode(b64))
        except (ValueError, TypeError) as exc:
            return Failure(
                MangaError(
                    kind=ErrorKind.IMAGE_CALL_FAILED,
                    message=f"failed to decode image b64: {exc}",
                )
            )

    def _generate_once(
        self,
        prompt: str,
        *,
        size: str,
        quality: str,
        model: str,
    ) -> Result[bytes, MangaError]:
        if self._client is None:
            return Failure(
                MangaError(
                    kind=ErrorKind.CONFIG_ERROR,
                    message=(
                        "OpenAI image client not initialized — set OPENAI_API_KEY "
                        "or pass api_key= to OpenAIImageClient"
                    ),
                )
            )
        try:
            # gpt-image-2 always returns b64 and rejects `response_format` —
            # only legacy DALL·E models accept that argument. Passing it here
            # would cause a non-retryable 400 on every real call.
            response = cast(
                "ImagesResponse",
                self._client.images.generate(
                    prompt=prompt,
                    model=model,
                    size=size,  # type: ignore[arg-type]
                    quality=quality,  # type: ignore[arg-type]
                ),
            )
        except RateLimitError as exc:
            return Failure(
                MangaError(
                    kind=ErrorKind.IMAGE_RATE_LIMITED,
                    message=f"OpenAI image rate limit: {exc}",
                )
            )
        except APIStatusError as exc:
            return Failure(self._classify_status_error(exc))
        except APIConnectionError as exc:
            return Failure(
                MangaError(
                    kind=ErrorKind.IMAGE_CALL_FAILED,
                    message=f"OpenAI image connection error: {exc}",
                )
            )
        return self._extract_bytes(response)

    def _edit_once(
        self,
        prompt: str,
        *,
        base: Path | None,
        refs: Sequence[Path],
        size: str,
        quality: str,
        model: str,
    ) -> Result[bytes, MangaError]:
        if self._client is None:
            return Failure(
                MangaError(
                    kind=ErrorKind.CONFIG_ERROR,
                    message=(
                        "OpenAI image client not initialized — set OPENAI_API_KEY "
                        "or pass api_key= to OpenAIImageClient"
                    ),
                )
            )
        # gpt-image-2 caps ref + base at 16. Caller should enforce this earlier
        # (it's a budget violation, not a transient failure), but guard here too.
        total = len(refs) + (1 if base is not None else 0)
        if total > 16:
            return Failure(
                MangaError(
                    kind=ErrorKind.REF_BUDGET_EXCEEDED,
                    message=f"ref budget exceeded: {total} > 16",
                    detail={"refs": len(refs), "has_base": base is not None},
                )
            )

        image_paths: list[Path] = []
        if base is not None:
            image_paths.append(base)
        image_paths.extend(refs)
        if not image_paths:
            return Failure(
                MangaError(
                    kind=ErrorKind.INVALID_STATE,
                    message="edit() requires at least base or one ref",
                )
            )

        # Open all files and pass file objects to the SDK; close on exit.
        opened: list[object] = []
        try:
            for p in image_paths:
                try:
                    opened.append(p.open("rb"))
                except OSError as exc:
                    return Failure(
                        MangaError(
                            kind=ErrorKind.IO_ERROR,
                            message=f"failed to open ref image: {exc}",
                            detail={"path": str(p)},
                        )
                    )
            try:
                # Same gpt-image-2 / response_format incompatibility as `generate`.
                response = cast(
                    "ImagesResponse",
                    self._client.images.edit(
                        prompt=prompt,
                        image=opened,  # type: ignore[arg-type]
                        model=model,
                        size=size,  # type: ignore[arg-type]
                        quality=quality,  # type: ignore[arg-type]
                    ),
                )
            except RateLimitError as exc:
                return Failure(
                    MangaError(
                        kind=ErrorKind.IMAGE_RATE_LIMITED,
                        message=f"OpenAI image rate limit: {exc}",
                    )
                )
            except APIStatusError as exc:
                return Failure(self._classify_status_error(exc))
            except APIConnectionError as exc:
                return Failure(
                    MangaError(
                        kind=ErrorKind.IMAGE_CALL_FAILED,
                        message=f"OpenAI image connection error: {exc}",
                    )
                )
        finally:
            for fh in opened:
                close = getattr(fh, "close", None)
                if callable(close):
                    close()

        return self._extract_bytes(response)

    def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1536",
        quality: str = "high",
        model: str = "gpt-image-2",
    ) -> Result[bytes, MangaError]:
        handler = RetryHandler(self.retry_config)
        return handler.execute(
            lambda: self._generate_once(prompt, size=size, quality=quality, model=model),
            operation_name=f"image_generate({model})",
        )

    def edit(
        self,
        prompt: str,
        *,
        base: Path | None = None,
        refs: Sequence[Path] = (),
        size: str = "1024x1536",
        quality: str = "high",
        model: str = "gpt-image-2",
    ) -> Result[bytes, MangaError]:
        handler = RetryHandler(self.retry_config)
        return handler.execute(
            lambda: self._edit_once(
                prompt,
                base=base,
                refs=refs,
                size=size,
                quality=quality,
                model=model,
            ),
            operation_name=f"image_edit({model})",
        )


__all__ = ["OpenAIImageClient"]
