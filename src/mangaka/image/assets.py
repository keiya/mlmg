"""Asset persistence: strict (canonical pipeline) + versioned (inject CLI).

Two write semantics:

- `save_bytes_strict(target, data)`: atomic O_CREAT|O_EXCL. Loud-fails with
  `FILE_EXISTS` if anything already sits at `target`. This is the canonical
  pipeline path — the pipeline never overwrites, and any drift between
  state JSON ("page N is unrendered") and disk (`page_N.png` already
  present) is a recovery signal, not something to silently version up.
  Race-safe under parallel workers writing to the same target by syscall
  guarantee — exactly one worker succeeds, the others get FILE_EXISTS.

- `save_bytes_versioned(target, data)`: legacy behavior. If `target`
  exists, write to `target_v002`, `target_v003`, etc. Used only by the
  `--inject-*` CLI (intentional update path).

The previous `save_bytes` helper is renamed to `save_bytes_versioned`; the
old symbol is kept as an alias pointing at the *versioned* path for one
cycle so external scripts in `tools/` keep working, and is deprecated.
New code should call one of the two explicitly named functions.
"""

from __future__ import annotations

import os
from pathlib import Path

from mangaka.errors import ErrorKind, MangaError
from mangaka.result import Failure, Result, Success


def next_available_path(target: Path) -> Path:
    """Return `target` if free, else `<stem>_v<NNN><ext>` where NNN starts at 002."""
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


def save_bytes_strict(target: Path, data: bytes) -> Result[Path, MangaError]:
    """Atomically write `data` to exactly `target`. Never overwrites.

    Uses `O_CREAT|O_EXCL`, which is atomic at the syscall level: either
    we create the file (and own its content) or we observe that it
    already exists and return `IO_ERROR`. The exists-branch returns a
    distinguishable error message so callers can react if needed.

    If `os.open` succeeds, ownership of the fd transfers to `os.fdopen`
    (no double-close). If `write` raises (e.g. disk full), the partial
    file is unlinked before re-raising so the next attempt sees a clean
    slate.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"refusing to overwrite existing asset at {target}",
                detail={"path": str(target), "reason": "FILE_EXISTS"},
            )
        )
    except OSError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"failed to open asset for writing: {exc}",
                detail={"path": str(target), "reason": "OPEN_FAILED"},
            )
        )

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except OSError as exc:
        # Disk full, EIO, broken NFS, etc. — clean up the partial file so
        # a retry sees a clear slate, then return a typed Failure per the
        # module's Result[Path, MangaError] contract.
        target.unlink(missing_ok=True)
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"failed to write asset: {exc}",
                detail={"path": str(target), "reason": "WRITE_FAILED"},
            )
        )
    except BaseException:
        # KeyboardInterrupt / SystemExit / asyncio CancelledError: clean up
        # the partial file but propagate the signal — these are programmer-
        # controlled escapes, not domain errors.
        target.unlink(missing_ok=True)
        raise
    return Success(target)


def save_bytes_versioned(target: Path, data: bytes) -> Result[Path, MangaError]:
    """Write `data` to a non-conflicting path near `target`.

    Uses `next_available_path` so existing files are never overwritten —
    a `_vNNN` sibling is chosen instead. Used by `--inject-*` CLI.
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


# Backwards-compat alias. Pipeline callers should migrate to
# save_bytes_strict; --inject-* CLI should call save_bytes_versioned
# directly. This alias is removed in a future cycle.
save_bytes = save_bytes_versioned


__all__ = [
    "next_available_path",
    "save_bytes",
    "save_bytes_strict",
    "save_bytes_versioned",
]
