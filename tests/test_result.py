"""Tests for the Result helpers in `mangaka.result`."""

from __future__ import annotations

import pytest
from returns.result import Failure, Result, Success

from mangaka.result import aggregate_results, unreachable


def test_aggregate_all_success() -> None:
    rs = [Success(1), Success(2), Success(3)]
    out = aggregate_results(rs)
    assert isinstance(out, Success)
    assert out.unwrap() == [1, 2, 3]


def test_aggregate_returns_first_failure() -> None:
    rs = [Success(1), Failure("boom"), Success(3)]
    out = aggregate_results(rs)
    assert isinstance(out, Failure)
    assert out.failure() == "boom"


def test_aggregate_empty_is_success_empty() -> None:
    empty: list[Result[int, str]] = []
    out = aggregate_results(empty)
    assert isinstance(out, Success)
    assert out.unwrap() == []


def test_unreachable_raises() -> None:
    with pytest.raises(RuntimeError, match="should not happen"):
        unreachable("should not happen")
