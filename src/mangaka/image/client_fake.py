"""Fake image client returning canned bytes for tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from mangaka.errors import ErrorKind, MangaError
from mangaka.result import Failure, Result, Success

# A 1×1 transparent PNG — enough to exercise the byte-passing path without
# pulling in PIL for fixture generation.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@dataclass(frozen=True)
class FakeImageCall:
    """Record of one generate / edit invocation."""

    method: str  # "generate" or "edit"
    prompt: str
    size: str
    quality: str
    model: str
    base: Path | None
    refs: tuple[Path, ...]


@dataclass
class FakeImageClient:
    """Test double for `ImageClient`.

    By default every call returns `Success(_TINY_PNG)`. Pre-load `results` to
    return mixed Success / Failure outcomes in sequence.
    """

    default_bytes: bytes = _TINY_PNG
    results: list[Result[bytes, MangaError]] = field(
        default_factory=list[Result[bytes, MangaError]]
    )
    calls: list[FakeImageCall] = field(default_factory=list[FakeImageCall])

    def _next(self) -> Result[bytes, MangaError]:
        if self.results:
            return self.results.pop(0)
        return Success(self.default_bytes)

    def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1536",
        quality: str = "high",
        model: str = "gpt-image-2",
    ) -> Result[bytes, MangaError]:
        self.calls.append(
            FakeImageCall(
                method="generate",
                prompt=prompt,
                size=size,
                quality=quality,
                model=model,
                base=None,
                refs=(),
            )
        )
        return self._next()

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
        self.calls.append(
            FakeImageCall(
                method="edit",
                prompt=prompt,
                size=size,
                quality=quality,
                model=model,
                base=base,
                refs=tuple(refs),
            )
        )
        return self._next()

    def with_failure(self, error: MangaError) -> FakeImageClient:
        """Variant that returns `Failure(error)` on every call."""
        return _AlwaysFailFakeImageClient(error=error)


@dataclass
class _AlwaysFailFakeImageClient(FakeImageClient):
    error: MangaError = field(
        default_factory=lambda: MangaError(
            kind=ErrorKind.IMAGE_CALL_FAILED, message="forced fake image failure"
        )
    )

    def _next(self) -> Result[bytes, MangaError]:
        return Failure(self.error)


__all__ = ["FakeImageCall", "FakeImageClient"]
