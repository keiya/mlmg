"""`ImageClient` Protocol.

Two methods so the intent is explicit:
- `generate`: text-only generation (e.g. the very first style ref).
- `edit`: generation with reference image(s); covers both "edit an existing
  base" and "compose a new image from refs" (the PageRender main case).

Both return raw PNG bytes wrapped in `Result[bytes, MangaError]`. Persistence
to disk and versioned naming live above this layer in layer code.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from mangaka.errors import MangaError
from mangaka.result import Result


class ImageClient(Protocol):
    """Pure-image generation Protocol.

    Implementations must return PNG bytes via `Result[bytes, MangaError]` and
    raise only on programmer bugs. Retries are the caller's responsibility.
    """

    def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1536",
        quality: str = "high",
        model: str = "gpt-image-2",
    ) -> Result[bytes, MangaError]: ...

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
        """Generate an image conditioned on `base` (optional) + `refs`.

        Combined ref budget (base + refs) must be ≤ 16 (gpt-image-2 limit).
        Layer code is responsible for the budget check before calling this.
        """
        ...


__all__ = ["ImageClient"]
