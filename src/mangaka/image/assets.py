"""Asset persistence with versioned naming.

Canonical artifacts in `assets/` are immutable: once `alice.png` is written,
later writes go to `alice_v002.png`, `alice_v003.png`, etc. The pipeline
itself writes only the first version (the un-suffixed name); the
`--inject-*` CLI is the only thing that creates `_vNNN` variants.

This module exposes one helper, `next_available_path(target)`, that returns
either `target` if it doesn't exist, or the next available `_vNNN` sibling.
"""

from __future__ import annotations

from pathlib import Path

from mangaka.errors import ErrorKind, MangaError
from mangaka.result import Failure, Result, Success


def next_available_path(target: Path) -> Path:
    """Return `target` if free, else `<stem>_v<NNN><ext>` where NNN starts at 002.

    If `target` already exists *and* `_v002` exists *and* `_v003` exists, etc.,
    keep incrementing until a free slot appears. The cap (999) is arbitrary;
    if someone has a thousand versions, something else is wrong.
    """
    if not target.exists():
        return target
    stem = target.stem
    ext = target.suffix
    parent = target.parent
    for n in range(2, 1000):
        candidate = parent / f"{stem}_v{n:03d}{ext}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"exhausted versioned slots for {target}")  # invariant


def save_bytes(target: Path, data: bytes) -> Result[Path, MangaError]:
    """Write `data` to a non-conflicting path near `target`.

    Always uses `next_available_path` so existing files are never overwritten.
    Parent directories are created. Returns the path actually written.
    """
    actual = next_available_path(target)
    try:
        actual.parent.mkdir(parents=True, exist_ok=True)
        actual.write_bytes(data)
    except OSError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"failed to save asset: {exc}",
                detail={"path": str(actual)},
            )
        )
    return Success(actual)


__all__ = ["next_available_path", "save_bytes"]
