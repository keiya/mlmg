"""Result type re-exports from the `returns` library.

mangaka standardizes on `Result[T, MangaError]` for any layer that performs
external I/O. See CLAUDE.md / mlsg2 AGENTS.md for the full discipline.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NoReturn

from returns.result import Failure, Result, Success, safe


def unreachable(msg: str = "unreachable code") -> NoReturn:
    """Signal that a code path must never be reached.

    Use in exhaustive match statements after handling every variant.
    """
    raise RuntimeError(msg)


def aggregate_results[T, E](results: Sequence[Result[T, E]]) -> Result[list[T], E]:
    """Collect a sequence of Results.

    Returns `Success([...])` if every item is a `Success`, otherwise returns
    the first `Failure` encountered.
    """
    values: list[T] = []
    for r in results:
        if isinstance(r, Failure):
            return r
        values.append(r.unwrap())
    return Success(values)


__all__ = [
    "Failure",
    "Result",
    "Success",
    "aggregate_results",
    "safe",
    "unreachable",
]
