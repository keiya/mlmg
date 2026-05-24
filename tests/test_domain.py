"""Unit tests for domain dataclasses.

Covers derived lookups on `MangaState` (`characters_by_id`, `locations_by_id`,
`pages_by_number`) and that frozen dataclasses behave as expected.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from mangaka.domain import (
    ArcPhase,
    Character,
    Location,
    MangaState,
    Page,
    PageBeat,
    PageOutline,
    PagePlan,
    Panel,
    SpeechIntent,
)


def _make_page(n: int) -> Page:
    panel = Panel(
        panel_no=1,
        size_hint="regular",
        visual="x",
        emotion="calm",
        camera=None,
        speech_intents=[],
        sfx=[],
    )
    beat = PageBeat(
        page_number=n,
        phase="セットアップ",
        location_id="rooftop",
        character_ids=["alice"],
        mood="quiet",
        continuity_note=None,
        panels=[panel],
        md_path=Path(f"runs/x/page_beats/page_beat_{n:03d}.md"),
    )
    return Page(page_number=n, beat=beat, image_path=None)


def test_state_lookups_are_indexed_by_id() -> None:
    alice = Character(
        id="alice",
        name="アリス",
        description="...",
        sheet_paths=[Path("a.png")],
    )
    bob = Character(
        id="bob",
        name="ボブ",
        description="...",
        sheet_paths=[Path("b.png")],
    )
    rooftop = Location(
        id="rooftop",
        name="屋上",
        description="...",
        sheet_path=Path("r.png"),
    )

    state = MangaState(
        seed_input="seed",
        run_name="run",
        characters=[alice, bob],
        locations=[rooftop],
        pages=[_make_page(1), _make_page(2), _make_page(3)],
    )

    assert state.characters_by_id["alice"] is alice
    assert state.characters_by_id["bob"] is bob
    assert state.locations_by_id["rooftop"] is rooftop
    assert state.pages_by_number[2].page_number == 2


def test_pages_by_number_uses_page_number_not_index() -> None:
    """Regression guard for the inject bug fixed in SCHEMA.md §11.

    `state.pages[-1]` is the latest *appended* page, not necessarily the
    page numerically before some target N. Lookup must go via page_number.
    """
    state = MangaState(
        seed_input="s",
        run_name="r",
        pages=[_make_page(1), _make_page(3), _make_page(2)],
    )
    assert state.pages_by_number[3].page_number == 3
    assert state.pages_by_number[2].page_number == 2


def test_page_plan_construction() -> None:
    plan = PagePlan(
        total_pages=2,
        arc=[ArcPhase(phase="セットアップ", start_page=1, end_page=2, summary="出会い")],
        page_outline=[
            PageOutline(
                page_number=1,
                phase="セットアップ",
                summary="主人公登場",
                character_ids=["alice"],
                location_id="rooftop",
            ),
            PageOutline(
                page_number=2,
                phase="セットアップ",
                summary="ボブ登場",
                character_ids=["alice", "bob"],
                location_id="rooftop",
            ),
        ],
    )
    assert plan.total_pages == 2
    assert plan.arc[0].end_page == 2


def test_speech_intent_register_optional() -> None:
    si = SpeechIntent(
        speaker_id="alice",
        bubble_type="dialogue",
        text="やる、絶対に。",
    )
    assert si.register is None


def test_frozen_dataclass_rejects_mutation() -> None:
    si = SpeechIntent(speaker_id="alice", bubble_type="dialogue", text="x")
    with pytest.raises((AttributeError, TypeError)):
        si.text = "y"  # type: ignore[misc]


def test_replace_invalidates_cached_lookups() -> None:
    """`dataclasses.replace` must yield a state whose lookups reflect the new lists.

    Without `__post_init__` clearing the cache, the replaced instance would
    keep the source's cached_property values and silently return the wrong
    character set. This is the immutability contract relied on by all layers.
    """
    alice = Character(id="alice", name="アリス", description="...", sheet_paths=[Path("a.png")])
    bob = Character(id="bob", name="ボブ", description="...", sheet_paths=[Path("b.png")])

    s1 = MangaState(seed_input="s", run_name="r", characters=[alice])
    # Touch the cache on s1 first — if replace shared __dict__ it would leak.
    _ = s1.characters_by_id
    s2 = dataclasses.replace(s1, characters=[alice, bob])

    assert set(s1.characters_by_id) == {"alice"}
    assert set(s2.characters_by_id) == {"alice", "bob"}
