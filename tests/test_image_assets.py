"""Tests for versioned asset save (`next_available_path` / `save_bytes`)."""

from __future__ import annotations

from pathlib import Path

from returns.result import Success

from mangaka.image.assets import next_available_path, save_bytes


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
