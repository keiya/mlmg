"""Parser for the PagePlan JSON layer output.

Two-stage:
1. **Pydantic parse**: extract JSON (handling ```json fences), validate field
   types, and coerce into the typed model.
2. **Structural validate**: enforce SCHEMA §6 cross-field invariants that
   don't fit naturally in Pydantic (arc contiguity, page-number monotonicity,
   ID references, etc.).

Both stages return `Result[T, MangaError]`. The layer code calls
`parse_page_plan_text(...)` for the end-to-end parse-and-validate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mangaka.domain import ArcPhase, PageOutline, PagePlan
from mangaka.errors import ErrorKind, MangaError
from mangaka.result import Failure, Result, Success

# Tolerant fence stripper: accepts ```json ... ``` or ``` ... ``` or bare JSON.
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL | re.MULTILINE)


class _ArcPhaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: str = Field(min_length=1)
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    summary: str = Field(min_length=1)


class _PageOutlineModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_number: int = Field(ge=1)
    phase: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    # Every page must list at least one character. PageBeat / Ref Builder
    # (M4) iterate `character_ids` to assemble image refs; an empty list
    # would starve them. Narration-only frames are still expressible at the
    # Panel level via the `narrator` speaker_id, not by emptying this list.
    character_ids: list[str] = Field(min_length=1)
    location_id: str = Field(min_length=1)


class _PagePlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_pages: int = Field(ge=1)
    arc: list[_ArcPhaseModel] = Field(min_length=1)
    page_outline: list[_PageOutlineModel] = Field(min_length=1)


def _strip_code_fence(text: str) -> str:
    """Strip a single surrounding ```json``` fence if present.

    Leading / trailing whitespace tolerated; if no fence matches, returns
    the text unchanged.
    """
    stripped = text.strip()
    m = _FENCE_RE.search(stripped)
    if m:
        return m.group(1).strip()
    return stripped


def parse_page_plan_text(
    text: str,
    *,
    max_pages: int,
    max_arc_phases: int,
    known_character_ids: Sequence[str],
    known_location_ids: Sequence[str],
) -> Result[PagePlan, MangaError]:
    """Parse and validate a PagePlan from LLM output text.

    All cross-field invariants from SCHEMA.md §6 are checked here. The
    returned `PagePlan` is safe to persist and to consume downstream.
    """
    payload = _strip_code_fence(text)

    try:
        raw: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.JSON_INVALID,
                message=f"PagePlan JSON parse failed: {exc}",
            )
        )

    if not isinstance(raw, dict):
        return Failure(
            MangaError(
                kind=ErrorKind.JSON_INVALID,
                message=(
                    "PagePlan JSON root must be an object, got "
                    f"{type(raw).__name__}"
                ),
            )
        )

    try:
        model = _PagePlanModel.model_validate(raw)
    except ValidationError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.VALIDATION_FAILED,
                message=f"PagePlan schema validation failed: {exc}",
            )
        )

    structural = _validate_structure(
        model,
        max_pages=max_pages,
        max_arc_phases=max_arc_phases,
        known_character_ids=set(known_character_ids),
        known_location_ids=set(known_location_ids),
    )
    if isinstance(structural, Failure):
        return Failure(structural.failure())

    return Success(_to_domain(model))


def _validation_failed(message: str, **detail: object) -> Failure[MangaError]:
    return Failure(
        MangaError(
            kind=ErrorKind.VALIDATION_FAILED,
            message=message,
            detail=dict(detail) if detail else None,
        )
    )


def _validate_limits(
    model: _PagePlanModel, *, max_pages: int, max_arc_phases: int
) -> Result[None, MangaError]:
    if model.total_pages > max_pages:
        return _validation_failed(
            f"total_pages={model.total_pages} exceeds max_pages={max_pages}",
            total_pages=model.total_pages,
            max_pages=max_pages,
        )
    if len(model.arc) > max_arc_phases:
        return _validation_failed(
            f"len(arc)={len(model.arc)} exceeds max_arc_phases={max_arc_phases}"
        )
    return Success(None)


def _validate_arc_contiguity(model: _PagePlanModel) -> Result[None, MangaError]:
    """arc[0].start_page=1, end>=start within phase, contiguous, last.end=total."""
    if model.arc[0].start_page != 1:
        return _validation_failed(
            f"arc[0].start_page must be 1, got {model.arc[0].start_page}"
        )
    for i, phase in enumerate(model.arc):
        if phase.end_page < phase.start_page:
            return _validation_failed(
                f"arc[{i}].end_page ({phase.end_page}) < "
                f"start_page ({phase.start_page})"
            )
        if i + 1 < len(model.arc):
            next_start = model.arc[i + 1].start_page
            if phase.end_page + 1 != next_start:
                return _validation_failed(
                    f"arc gap/overlap: arc[{i}].end_page={phase.end_page}, "
                    f"arc[{i + 1}].start_page={next_start} — must be contiguous"
                )
    if model.arc[-1].end_page != model.total_pages:
        return _validation_failed(
            f"arc[-1].end_page ({model.arc[-1].end_page}) must equal "
            f"total_pages ({model.total_pages})"
        )
    return Success(None)


def _validate_page_outline(model: _PagePlanModel) -> Result[None, MangaError]:
    """len == total_pages, page_numbers are 1..N contiguous, phases in arc."""
    if len(model.page_outline) != model.total_pages:
        return _validation_failed(
            f"len(page_outline)={len(model.page_outline)} != "
            f"total_pages={model.total_pages}"
        )
    for i, po in enumerate(model.page_outline):
        if po.page_number != i + 1:
            return _validation_failed(
                f"page_outline[{i}].page_number={po.page_number}, "
                f"expected {i + 1} (1-indexed, contiguous)"
            )
    # Each outline's phase must match the SPECIFIC arc segment whose page
    # range covers its page_number — not merely exist somewhere in arc.
    # Otherwise an outline at page 1 could carry the last phase's label
    # despite belonging to the first arc segment.
    for po in model.page_outline:
        containing_arc = next(
            (a for a in model.arc if a.start_page <= po.page_number <= a.end_page),
            None,
        )
        if containing_arc is None:
            # Arc contiguity is already validated (start=1, contiguous, end=total),
            # so this is an invariant violation more than a user error.
            return _validation_failed(
                f"page_outline[page={po.page_number}] falls outside every arc "
                f"segment — internal invariant broken"
            )
        if po.phase != containing_arc.phase:
            return _validation_failed(
                f"page_outline[page={po.page_number}].phase={po.phase!r} does "
                f"not match arc segment "
                f"({containing_arc.start_page}-{containing_arc.end_page}) "
                f"phase={containing_arc.phase!r}"
            )
    return Success(None)


def _validate_id_references(
    model: _PagePlanModel,
    *,
    known_character_ids: set[str],
    known_location_ids: set[str],
) -> Result[None, MangaError]:
    for po in model.page_outline:
        for cid in po.character_ids:
            if cid not in known_character_ids:
                return _validation_failed(
                    f"page_outline[page={po.page_number}] references "
                    f"unknown character_id={cid!r}",
                    character_id=cid,
                )
        if po.location_id not in known_location_ids:
            return _validation_failed(
                f"page_outline[page={po.page_number}] references "
                f"unknown location_id={po.location_id!r}",
                location_id=po.location_id,
            )
    return Success(None)


def _validate_structure(
    model: _PagePlanModel,
    *,
    max_pages: int,
    max_arc_phases: int,
    known_character_ids: set[str],
    known_location_ids: set[str],
) -> Result[None, MangaError]:
    """Apply SCHEMA §6 cross-field rules. Returns Success(None) on pass."""
    for step in (
        _validate_limits(model, max_pages=max_pages, max_arc_phases=max_arc_phases),
        _validate_arc_contiguity(model),
        _validate_page_outline(model),
        _validate_id_references(
            model,
            known_character_ids=known_character_ids,
            known_location_ids=known_location_ids,
        ),
    ):
        if isinstance(step, Failure):
            return step
    return Success(None)


def _to_domain(model: _PagePlanModel) -> PagePlan:
    """Convert the pydantic shape to the frozen domain dataclass."""
    return PagePlan(
        total_pages=model.total_pages,
        arc=[
            ArcPhase(
                phase=p.phase,
                start_page=p.start_page,
                end_page=p.end_page,
                summary=p.summary,
            )
            for p in model.arc
        ],
        page_outline=[
            PageOutline(
                page_number=po.page_number,
                phase=po.phase,
                summary=po.summary,
                character_ids=list(po.character_ids),
                location_id=po.location_id,
            )
            for po in model.page_outline
        ],
    )


# Re-exported for inject CLI later (M5): parse from a pre-loaded dict.
def parse_page_plan_dict(
    data: dict[str, Any],
    *,
    max_pages: int,
    max_arc_phases: int,
    known_character_ids: Sequence[str],
    known_location_ids: Sequence[str],
) -> Result[PagePlan, MangaError]:
    return parse_page_plan_text(
        json.dumps(data),
        max_pages=max_pages,
        max_arc_phases=max_arc_phases,
        known_character_ids=known_character_ids,
        known_location_ids=known_location_ids,
    )


__all__ = ["parse_page_plan_dict", "parse_page_plan_text"]
