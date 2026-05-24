"""Domain types for the mangaka pipeline.

These dataclasses match ARCHITECTURE.md §domain types. They are pure data;
behavior lives in `layers/`, `image/`, `persistence.py`. Mutations go through
constructing a new `MangaState`, not in-place writes.

`T | None` on `MangaState` represents "this layer has not run yet". Missing
external resources are surfaced as `Failure(MangaError)`, not `None`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

# ---------------------------------------------------------------------------
# Text-only layers (Plot / Backstory / MPBV)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class MasterPlot:
    """Top-level plot synopsis produced by layer 1."""

    raw_markdown: str


@dataclass(slots=True, frozen=True)
class Backstories:
    """Backstory bundle for the cast produced by layer 2."""

    raw_markdown: str


@dataclass(slots=True, frozen=True)
class MPBV:
    """Multi-Pass Beat Validation output (validated story spine, layer 3)."""

    raw_markdown: str


# ---------------------------------------------------------------------------
# Stylist / Character / Location (text + image sub-step)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class Stylist:
    """Art-direction guide + the canonical style reference image."""

    raw_markdown: str
    style_ref_path: Path


@dataclass(slots=True, frozen=True)
class Character:
    """A character with stable id, text description, and one or more sheets."""

    id: str
    name: str
    description: str
    sheet_paths: list[Path]


@dataclass(slots=True, frozen=True)
class Location:
    """A location with stable id, text description, and a setting sheet."""

    id: str
    name: str
    description: str
    sheet_path: Path


# ---------------------------------------------------------------------------
# PagePlan (replaces mlsg2 Chapter + Timeline for short manga)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class ArcPhase:
    """One slice of the story arc (起承転結 / 3-5 phases)."""

    phase: str
    start_page: int
    end_page: int
    summary: str


@dataclass(slots=True, frozen=True)
class PageOutline:
    """Per-page outline (1-line summary + which chars/loc appear)."""

    page_number: int
    phase: str
    summary: str
    character_ids: list[str]
    location_id: str


@dataclass(slots=True, frozen=True)
class PagePlan:
    """Whole-story plan: arc phases + per-page outlines."""

    total_pages: int
    arc: list[ArcPhase]
    page_outline: list[PageOutline]


# ---------------------------------------------------------------------------
# Page (per-page image artifact)
# ---------------------------------------------------------------------------
#
# Historical note (PoC 2026-05-24): there used to be a PageBeat layer between
# PagePlan and PageRender that produced per-panel structured directives
# (visual / camera / speech_intents / sfx) in Markdown + YAML frontmatter.
# It was removed when PoC showed gpt-image-2 produces stronger narrative pages
# when given the PagePlan.page_outline.summary directly. See
# `docs/ARCHITECTURE.md` 設計の進化 for details. `Page` now only carries the
# rendered image path; semantic data for the page lives in
# `state.page_plan.page_outline[page_number - 1]`.


@dataclass(slots=True, frozen=True)
class Page:
    """A finished page — image path only; semantics live in PagePlan.page_outline."""

    page_number: int
    image_path: Path | None


# ---------------------------------------------------------------------------
# MangaState (pipeline accumulator)
# ---------------------------------------------------------------------------


# NOTE: no slots — cached_property needs instance __dict__, which slots removes.
@dataclass
class MangaState:
    """Pipeline accumulator. Layers return a *new* instance, never mutate.

    `T | None` on layer-output fields indicates "not yet run". Derived
    lookups (`characters_by_id`, etc.) are computed via `cached_property`.

    Immutability contract: pipeline layers must build a fresh state via
    `dataclasses.replace(state, ...)` rather than appending into
    `state.characters` / `state.pages` / `state.locations` in place. In-place
    mutation will leave the cached `*_by_id` / `pages_by_number` dicts stale.
    `__post_init__` clears these caches so `dataclasses.replace` is safe — but
    nothing prevents `state.pages.append(...)` at runtime; tests guard the
    intended usage.
    """

    seed_input: str
    run_name: str
    master_plot: MasterPlot | None = None
    backstories: Backstories | None = None
    mpbv: MPBV | None = None
    stylist: Stylist | None = None
    characters: list[Character] = field(default_factory=list[Character])
    locations: list[Location] = field(default_factory=list[Location])
    page_plan: PagePlan | None = None
    pages: list[Page] = field(default_factory=list[Page])

    # Raw LLM markdown captured BEFORE any image call. Resume entering
    # character / location layer reuses this cached text instead of re-
    # calling the LLM, so per-character / per-location `id`s stay stable
    # across resumes (the LLM is stochastic). Populated by the text phase
    # of each layer; consumed on resume. See docs/plans/
    # parallel_image_generation.md §3.8.
    character_markdown: str | None = None
    location_markdown: str | None = None

    def __post_init__(self) -> None:
        # `dataclasses.replace` reuses `__dict__` slots from the source for
        # field values but calls `__init__` / `__post_init__` on the new
        # instance — clear cached_property values so the new instance recomputes.
        for cached in ("characters_by_id", "locations_by_id", "pages_by_number"):
            self.__dict__.pop(cached, None)

    @cached_property
    def characters_by_id(self) -> dict[str, Character]:
        return {c.id: c for c in self.characters}

    @cached_property
    def locations_by_id(self) -> dict[str, Location]:
        return {loc.id: loc for loc in self.locations}

    @cached_property
    def pages_by_number(self) -> dict[int, Page]:
        return {p.page_number: p for p in self.pages}


__all__ = [
    "MPBV",
    "ArcPhase",
    "Backstories",
    "Character",
    "Location",
    "MangaState",
    "MasterPlot",
    "Page",
    "PageOutline",
    "PagePlan",
    "Stylist",
]
