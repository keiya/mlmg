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
# PageBeat / Panel (per-page directive consumed by PageRender)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class SpeechIntent:
    """Verbatim short dialogue for a bubble.

    `text` is the actual line to be drawn inside the bubble (≤ ~30 chars,
    Japanese). The PoC found that asking the image model to "translate intent
    into a line" produced garbled output — see `docs/PLAN.md` PoC notes —
    so we now pass exact text and let the renderer copy it verbatim.
    """

    speaker_id: str
    bubble_type: str
    text: str
    register: str | None = None


@dataclass(slots=True, frozen=True)
class SFX:
    """Drawn sound effect (e.g. カタカナ onomatopoeia)."""

    text: str
    role: str


@dataclass(slots=True, frozen=True)
class Panel:
    """One panel in reading order (top-right → bottom-left, 1-indexed)."""

    panel_no: int
    size_hint: str
    visual: str
    emotion: str
    camera: str | None
    speech_intents: list[SpeechIntent]
    sfx: list[SFX]


@dataclass(slots=True, frozen=True)
class PageBeat:
    """Parsed, structured per-page directive backed by a canonical .md file."""

    page_number: int
    phase: str
    location_id: str
    character_ids: list[str]
    mood: str
    continuity_note: str | None
    panels: list[Panel]
    md_path: Path


@dataclass(slots=True, frozen=True)
class Page:
    """A finished page: its beat plus the rendered image path (when complete)."""

    page_number: int
    beat: PageBeat
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
    "SFX",
    "ArcPhase",
    "Backstories",
    "Character",
    "Location",
    "MangaState",
    "MasterPlot",
    "Page",
    "PageBeat",
    "PageOutline",
    "PagePlan",
    "Panel",
    "SpeechIntent",
    "Stylist",
]
