"""Tests for the PagePlan JSON parser + SCHEMA §6 validators."""

from __future__ import annotations

import textwrap

from returns.result import Failure, Result, Success

from mangaka.domain import PagePlan
from mangaka.errors import ErrorKind, MangaError
from mangaka.parse.page_plan import parse_page_plan_text

# Compact valid PagePlan for 2 pages: arc has 1 phase, outline has 2 entries.
_VALID_JSON = textwrap.dedent(
    """\
    {
      "total_pages": 2,
      "arc": [
        {"phase": "起", "start_page": 1, "end_page": 2, "summary": "導入"}
      ],
      "page_outline": [
        {"page_number": 1, "phase": "起", "summary": "p1",
         "character_ids": ["alice"], "location_id": "rooftop"},
        {"page_number": 2, "phase": "起", "summary": "p2",
         "character_ids": ["alice", "bob"], "location_id": "rooftop"}
      ]
    }
    """
)


def _parse(text: str, total_max: int = 24) -> Result[PagePlan, MangaError]:
    return parse_page_plan_text(
        text,
        max_pages=total_max,
        max_arc_phases=5,
        known_character_ids=["alice", "bob"],
        known_location_ids=["rooftop"],
    )


# ---------------------------------------------------------------------------
# Happy path + fence handling
# ---------------------------------------------------------------------------


def test_parses_valid_plain_json() -> None:
    result = _parse(_VALID_JSON)
    assert isinstance(result, Success)
    pp = result.unwrap()
    assert pp.total_pages == 2
    assert len(pp.arc) == 1
    assert len(pp.page_outline) == 2


def test_parses_json_with_fence() -> None:
    fenced = f"```json\n{_VALID_JSON.strip()}\n```"
    result = _parse(fenced)
    assert isinstance(result, Success)


def test_parses_json_with_bare_fence() -> None:
    fenced = f"```\n{_VALID_JSON.strip()}\n```"
    result = _parse(fenced)
    assert isinstance(result, Success)


# ---------------------------------------------------------------------------
# Invalid JSON shape
# ---------------------------------------------------------------------------


def test_malformed_json_fails() -> None:
    result = _parse("{ not json")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.JSON_INVALID


def test_array_root_fails() -> None:
    result = _parse("[]")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.JSON_INVALID


def test_missing_required_fields_fails() -> None:
    result = _parse('{"total_pages": 2}')
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.VALIDATION_FAILED


# ---------------------------------------------------------------------------
# SCHEMA §6 structural rules
# ---------------------------------------------------------------------------


def test_total_pages_exceeds_max_fails() -> None:
    result = _parse(_VALID_JSON, total_max=1)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.VALIDATION_FAILED
    assert "max_pages" in result.failure().message


def test_arc_first_must_start_at_1() -> None:
    bad = _VALID_JSON.replace('"start_page": 1', '"start_page": 2', 1)
    result = _parse(bad)
    assert isinstance(result, Failure)
    assert "start_page must be 1" in result.failure().message


def test_arc_last_must_end_at_total_pages() -> None:
    bad = textwrap.dedent(
        """\
        {
          "total_pages": 3,
          "arc": [
            {"phase": "起", "start_page": 1, "end_page": 2, "summary": "x"}
          ],
          "page_outline": [
            {"page_number": 1, "phase": "起", "summary": "a",
             "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 2, "phase": "起", "summary": "b",
             "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 3, "phase": "起", "summary": "c",
             "character_ids": ["alice"], "location_id": "rooftop"}
          ]
        }
        """
    )
    result = _parse(bad)
    assert isinstance(result, Failure)
    assert "must equal" in result.failure().message
    assert "total_pages" in result.failure().message


def test_arc_gap_between_phases_fails() -> None:
    bad = textwrap.dedent(
        """\
        {
          "total_pages": 4,
          "arc": [
            {"phase": "起", "start_page": 1, "end_page": 2, "summary": "x"},
            {"phase": "結", "start_page": 4, "end_page": 4, "summary": "y"}
          ],
          "page_outline": [
            {"page_number": 1, "phase": "起", "summary": "a", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 2, "phase": "起", "summary": "b", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 3, "phase": "起", "summary": "c", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 4, "phase": "結", "summary": "d", "character_ids": ["alice"], "location_id": "rooftop"}
          ]
        }
        """
    )
    result = _parse(bad)
    assert isinstance(result, Failure)
    assert "contiguous" in result.failure().message or "gap/overlap" in result.failure().message


def test_arc_phase_end_before_start_fails() -> None:
    bad = _VALID_JSON.replace('"end_page": 2', '"end_page": 0', 1)
    # Replacement may target arc.end_page or outline; ensure arc match by being specific.
    # Easier: write a fresh JSON.
    bad = textwrap.dedent(
        """\
        {
          "total_pages": 2,
          "arc": [
            {"phase": "起", "start_page": 2, "end_page": 1, "summary": "bad"}
          ],
          "page_outline": [
            {"page_number": 1, "phase": "起", "summary": "p1", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 2, "phase": "起", "summary": "p2", "character_ids": ["alice"], "location_id": "rooftop"}
          ]
        }
        """
    )
    result = _parse(bad)
    assert isinstance(result, Failure)


def test_page_outline_length_mismatch_fails() -> None:
    bad = textwrap.dedent(
        """\
        {
          "total_pages": 2,
          "arc": [{"phase": "起", "start_page": 1, "end_page": 2, "summary": "x"}],
          "page_outline": [
            {"page_number": 1, "phase": "起", "summary": "p1", "character_ids": ["alice"], "location_id": "rooftop"}
          ]
        }
        """
    )
    result = _parse(bad)
    assert isinstance(result, Failure)
    assert "page_outline" in result.failure().message


def test_page_outline_page_numbers_not_contiguous_fails() -> None:
    """page_outline[i].page_number must equal i + 1 (1-indexed, no gaps, no skips)."""
    bad = textwrap.dedent(
        """\
        {
          "total_pages": 2,
          "arc": [{"phase": "起", "start_page": 1, "end_page": 2, "summary": "x"}],
          "page_outline": [
            {"page_number": 1, "phase": "起", "summary": "p1", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 3, "phase": "起", "summary": "p3", "character_ids": ["alice"], "location_id": "rooftop"}
          ]
        }
        """
    )
    result = _parse(bad)
    assert isinstance(result, Failure)
    assert "page_number" in result.failure().message


def test_outline_phase_must_match_containing_arc_segment() -> None:
    """Round-3 review fix: phase must match the arc segment for that page,
    not just exist somewhere in arc. arc 起=1-2, 結=3-4 with page 1 carrying
    phase=結 must fail even though 結 exists in arc.
    """
    bad = textwrap.dedent(
        """\
        {
          "total_pages": 4,
          "arc": [
            {"phase": "起", "start_page": 1, "end_page": 2, "summary": "x"},
            {"phase": "結", "start_page": 3, "end_page": 4, "summary": "y"}
          ],
          "page_outline": [
            {"page_number": 1, "phase": "結", "summary": "wrong phase for page 1",
             "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 2, "phase": "起", "summary": "p2", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 3, "phase": "結", "summary": "p3", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 4, "phase": "結", "summary": "p4", "character_ids": ["alice"], "location_id": "rooftop"}
          ]
        }
        """
    )
    result = _parse(bad)
    assert isinstance(result, Failure)
    msg = result.failure().message
    assert "page_outline[page=1]" in msg
    assert "arc segment" in msg


def test_outline_phase_not_in_arc_fails() -> None:
    bad = _VALID_JSON.replace('"phase": "起",', '"phase": "結",', 1)  # change first outline
    # First occurrence is the arc phase — instead swap explicitly.
    bad = textwrap.dedent(
        """\
        {
          "total_pages": 2,
          "arc": [{"phase": "起", "start_page": 1, "end_page": 2, "summary": "x"}],
          "page_outline": [
            {"page_number": 1, "phase": "結", "summary": "p1", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 2, "phase": "起", "summary": "p2", "character_ids": ["alice"], "location_id": "rooftop"}
          ]
        }
        """
    )
    result = _parse(bad)
    assert isinstance(result, Failure)
    # Round-3 fix tightened the check to "must match the containing arc
    # segment", which subsumes the old "phase in arc" rule. Phase "結" no
    # longer exists in arc at all in this fixture, so the segment-level
    # message fires.
    msg = result.failure().message
    assert "page_outline[page=1]" in msg
    assert "arc segment" in msg or "not in arc phases" in msg


def test_unknown_character_id_fails() -> None:
    bad = _VALID_JSON.replace('"character_ids": ["alice"]', '"character_ids": ["unknown"]', 1)
    result = _parse(bad)
    assert isinstance(result, Failure)
    assert "unknown character_id" in result.failure().message


def test_unknown_location_id_fails() -> None:
    bad = _VALID_JSON.replace('"location_id": "rooftop"', '"location_id": "void"', 1)
    result = _parse(bad)
    assert isinstance(result, Failure)
    assert "unknown location_id" in result.failure().message


def test_empty_character_ids_rejected() -> None:
    """Round-1 review fix: a page must list at least one character.

    PageBeat (M4) iterates `character_ids` for ref-image assembly — an
    empty list silently starves the ref builder.
    """
    bad = textwrap.dedent(
        """\
        {
          "total_pages": 1,
          "arc": [{"phase": "起", "start_page": 1, "end_page": 1, "summary": "x"}],
          "page_outline": [
            {"page_number": 1, "phase": "起", "summary": "p1",
             "character_ids": [], "location_id": "rooftop"}
          ]
        }
        """
    )
    result = _parse(bad)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.VALIDATION_FAILED


def test_too_many_arc_phases_fails() -> None:
    """`max_arc_phases` cap."""
    six_phases = textwrap.dedent(
        """\
        {
          "total_pages": 6,
          "arc": [
            {"phase": "1", "start_page": 1, "end_page": 1, "summary": "x"},
            {"phase": "2", "start_page": 2, "end_page": 2, "summary": "x"},
            {"phase": "3", "start_page": 3, "end_page": 3, "summary": "x"},
            {"phase": "4", "start_page": 4, "end_page": 4, "summary": "x"},
            {"phase": "5", "start_page": 5, "end_page": 5, "summary": "x"},
            {"phase": "6", "start_page": 6, "end_page": 6, "summary": "x"}
          ],
          "page_outline": [
            {"page_number": 1, "phase": "1", "summary": "a", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 2, "phase": "2", "summary": "a", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 3, "phase": "3", "summary": "a", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 4, "phase": "4", "summary": "a", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 5, "phase": "5", "summary": "a", "character_ids": ["alice"], "location_id": "rooftop"},
            {"page_number": 6, "phase": "6", "summary": "a", "character_ids": ["alice"], "location_id": "rooftop"}
          ]
        }
        """
    )
    result = _parse(six_phases)
    assert isinstance(result, Failure)
    assert "max_arc_phases" in result.failure().message
