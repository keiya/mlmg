"""Tests for asset save helpers (versioned + strict)."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from returns.result import Failure, Success

from mangaka.image.assets import (
    next_available_path,
    save_bytes,
    save_bytes_strict,
    save_bytes_versioned,
)


def test_first_save_uses_unsuffixed_name(tmp_path: Path) -> None:
    target = tmp_path / "assets" / "characters" / "alice.png"
    result = save_bytes(target, b"PNG1")
    assert isinstance(result, Success)
    assert result.unwrap().name == "alice.png"
    assert result.unwrap().read_bytes() == b"PNG1"


def test_second_save_uses_v002(tmp_path: Path) -> None:
    """Versioned naming: existing files are never overwritten."""
    target = tmp_path / "alice.png"
    target.write_bytes(b"original")

    result = save_bytes(target, b"new")
    assert isinstance(result, Success)
    actual = result.unwrap()
    assert actual.name == "alice_v002.png"
    assert actual.read_bytes() == b"new"
    # Original untouched.
    assert target.read_bytes() == b"original"


def test_third_save_uses_v003(tmp_path: Path) -> None:
    target = tmp_path / "alice.png"
    target.write_bytes(b"1")
    (tmp_path / "alice_v002.png").write_bytes(b"2")

    result = save_bytes(target, b"3")
    assert isinstance(result, Success)
    assert result.unwrap().name == "alice_v003.png"


def test_next_available_returns_target_when_free(tmp_path: Path) -> None:
    p = tmp_path / "free.png"
    assert next_available_path(p) == p


def test_next_available_creates_parent_on_save(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "x.png"
    result = save_bytes(target, b"x")
    assert isinstance(result, Success)
    assert target.exists()


def test_save_bytes_alias_routes_to_versioned() -> None:
    """`save_bytes` is kept as a backwards-compat alias for the versioned
    save path until external callers migrate."""
    assert save_bytes is save_bytes_versioned


# --- save_bytes_strict -----------------------------------------------------


def test_strict_writes_target_when_free(tmp_path: Path) -> None:
    target = tmp_path / "assets" / "x.png"
    result = save_bytes_strict(target, b"abc")
    assert isinstance(result, Success)
    assert result.unwrap() == target
    assert target.read_bytes() == b"abc"


def test_strict_fails_loudly_when_target_exists(tmp_path: Path) -> None:
    """Strict save must not silently version up — that masks drift."""
    target = tmp_path / "x.png"
    target.write_bytes(b"original")
    result = save_bytes_strict(target, b"new")
    assert isinstance(result, Failure)
    err = result.failure()
    assert "refusing to overwrite" in err.message
    assert err.detail is not None
    assert err.detail["reason"] == "FILE_EXISTS"
    # Original untouched — atomic guarantee.
    assert target.read_bytes() == b"original"


def test_strict_creates_parent(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "nested" / "x.png"
    result = save_bytes_strict(target, b"x")
    assert isinstance(result, Success)
    assert target.exists()


def test_strict_wraps_write_failure_into_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Post-O_EXCL OSError (disk full, EIO, NFS) must surface as
    Failure(MangaError) — not raise. Otherwise callers that already use
    the Result contract crash on filesystem issues."""
    target = tmp_path / "x.png"
    # The real file is already created by os.open(O_CREAT|O_EXCL) before
    # fdopen runs. We replace fdopen with a writer that always raises;
    # save_bytes_strict must unlink + wrap the OSError into Failure.
    real_fdopen = os.fdopen

    def boom_fdopen(fd: int, mode: str) -> object:
        real_fdopen(fd, mode).close()  # close the real fd so it isn't leaked

        class _Boom:
            def write(self, _: bytes) -> int:
                raise OSError(28, "No space left on device")

            def __enter__(self) -> _Boom:
                return self

            def __exit__(self, *_a: object) -> bool:
                return False

        return _Boom()

    monkeypatch.setattr(os, "fdopen", boom_fdopen)
    result = save_bytes_strict(target, b"data")

    assert isinstance(result, Failure)
    err = result.failure()
    assert err.detail is not None
    assert err.detail["reason"] == "WRITE_FAILED"
    # Partial file cleaned up.
    assert not target.exists()


def test_strict_concurrent_same_target_only_one_wins(tmp_path: Path) -> None:
    """Under N threads racing to write the same path, exactly one Success.

    Critically, the winner's bytes must survive — losers can't clobber
    the winner's write because `O_EXCL` makes them fail before opening.
    """
    from mangaka.errors import MangaError

    target = tmp_path / "racy.png"
    barrier = threading.Barrier(parties=16)
    winning_payload: list[bytes] = []  # singleton via lock
    failure_reasons: list[str] = []
    outcomes_lock = threading.Lock()

    def worker(payload: bytes) -> None:
        barrier.wait()  # release all 16 at once
        result = save_bytes_strict(target, payload)
        with outcomes_lock:
            if isinstance(result, Success):
                winning_payload.append(payload)
            else:
                err: MangaError = result.failure()
                detail = err.detail or {}
                failure_reasons.append(str(detail.get("reason", "")))

    threads = [
        threading.Thread(target=worker, args=(f"worker{i}".encode(),))
        for i in range(16)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winning_payload) == 1, f"expected exactly 1 winner, got {len(winning_payload)}"
    assert len(failure_reasons) == 15
    # Every loser must report FILE_EXISTS — never some other OS error.
    for reason in failure_reasons:
        assert reason == "FILE_EXISTS"
    # Winner's payload must survive on disk — losers can't clobber it.
    assert target.read_bytes() == winning_payload[0]
