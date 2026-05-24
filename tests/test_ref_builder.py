"""Tests for `build_refs` priority + budget logic."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from pathlib import Path

from mangaka.domain import (
    Character,
    Location,
    MangaState,
    Page,
    PageOutline,
    Stylist,
)
from mangaka.image.ref_builder import LabeledRef, build_refs


def _outline(*, page_number: int, location_id: str, character_ids: list[str]) -> PageOutline:
    return PageOutline(
        page_number=page_number,
        phase="起",
        summary="x",
        character_ids=character_ids,
        location_id=location_id,
    )


def _state(tmp_path: Path, *, n_characters: int = 2) -> MangaState:
    style_ref = tmp_path / "style.png"
    style_ref.write_bytes(b"x")
    return MangaState(
        seed_input="s",
        run_name="r",
        stylist=Stylist(raw_markdown="g", style_ref_path=style_ref),
        characters=[
            Character(
                id=f"c{i}",
                name=f"キャラ{i}",
                description="d",
                sheet_paths=[tmp_path / f"c{i}.png"],
            )
            for i in range(n_characters)
        ],
        locations=[
            Location(
                id="rooftop",
                name="屋上",
                description="d",
                sheet_path=tmp_path / "rooftop.png",
            ),
        ],
    )


def test_minimal_refs_style_then_loc(tmp_path: Path) -> None:
    state = _state(tmp_path, n_characters=0)
    outline = _outline(page_number=1, location_id="rooftop", character_ids=[])
    refs = build_refs(state, outline, max_refs=16, include_prev=False)
    assert [r.label for r in refs] == [
        "スタイル参照画",
        "場所「屋上」の設定画",
    ]


def test_full_order_style_loc_prev_chars(tmp_path: Path) -> None:
    state = _state(tmp_path, n_characters=2)
    prev_image = tmp_path / "page_001.png"
    prev_image.write_bytes(b"prev")
    prev_page = Page(page_number=1, image_path=prev_image)
    state = dc_replace(state, pages=[prev_page])

    outline = _outline(page_number=2, location_id="rooftop", character_ids=["c0", "c1"])
    refs = build_refs(state, outline, max_refs=16, include_prev=True)

    labels = [r.label for r in refs]
    assert labels[0] == "スタイル参照画"
    assert labels[1].startswith("場所")
    assert labels[2].startswith("直前ページ")
    assert "キャラ0" in labels[3]
    assert "キャラ1" in labels[4]


def test_previous_page_uses_page_number_minus_one_not_pages_last(tmp_path: Path) -> None:
    """state.pages[-1] is the LAST APPENDED page, not necessarily page (N-1).
    Inject reorders state.pages — build_refs must use page_number-based lookup.
    """
    state = _state(tmp_path, n_characters=1)
    p1_image = tmp_path / "p1.png"
    p2_image = tmp_path / "p2.png"
    p3_image = tmp_path / "p3.png"
    for p in (p1_image, p2_image, p3_image):
        p.write_bytes(b"x")

    # pages appended out of order: 3, 1, 2 (simulates inject reordering).
    pages = [
        Page(page_number=3, image_path=p3_image),
        Page(page_number=1, image_path=p1_image),
        Page(page_number=2, image_path=p2_image),
    ]
    state = dc_replace(state, pages=pages)

    outline = _outline(page_number=3, location_id="rooftop", character_ids=["c0"])
    refs = build_refs(state, outline, max_refs=16, include_prev=True)
    prev_ref = next((r for r in refs if r.label.startswith("直前ページ")), None)
    assert prev_ref is not None
    assert prev_ref.path == p2_image  # page_number=2's image, not state.pages[-1].

    # Now ask for page 5 (no previous page exists in state) — must NOT include a prev ref.
    outline_5 = _outline(page_number=5, location_id="rooftop", character_ids=["c0"])
    refs_5 = build_refs(state, outline_5, max_refs=16, include_prev=True)
    assert all(not r.label.startswith("直前ページ") for r in refs_5)


def test_character_budget_truncates_from_tail(tmp_path: Path) -> None:
    """Exceeding `max_refs` drops chars from the END of character_ids."""
    state = _state(tmp_path, n_characters=8)
    outline = _outline(
        page_number=1, location_id="rooftop", character_ids=[f"c{i}" for i in range(8)]
    )
    # max_refs = 4 → 1 style + 1 loc + 2 char slots; chars c0, c1 survive.
    refs = build_refs(state, outline, max_refs=4, include_prev=False)
    assert len(refs) == 4
    char_labels = [r.label for r in refs[2:]]
    assert "キャラ0" in char_labels[0]
    assert "キャラ1" in char_labels[1]


def test_negative_char_budget_clamped_when_refs_full(tmp_path: Path) -> None:
    """If reserved slots (style + loc + prev) already meet/exceed max_refs,
    char_budget is 0 — no chars included, never negative slicing weirdness.
    """
    state = _state(tmp_path, n_characters=2)
    prev_image = tmp_path / "p1.png"
    prev_image.write_bytes(b"x")
    prev_page = Page(page_number=1, image_path=prev_image)
    state = dc_replace(state, pages=[prev_page])

    outline = _outline(page_number=2, location_id="rooftop", character_ids=["c0", "c1"])
    # max_refs=3 → exactly style + loc + prev, zero room for characters.
    refs = build_refs(state, outline, max_refs=3, include_prev=True)
    assert len(refs) == 3
    assert all("キャラ" not in r.label for r in refs)


def test_min_max_refs_with_prev_does_not_crash(tmp_path: Path) -> None:
    """`max_refs=2` + `include_prev=true` past page 1 used to bust
    `assert len(refs) <= max_refs`. Now prev is dropped when no slot would
    remain — style + loc are the absolute floor.
    """
    state = _state(tmp_path, n_characters=2)
    prev_image = tmp_path / "page_001.png"
    prev_image.write_bytes(b"x")
    state = dc_replace(state, pages=[Page(page_number=1, image_path=prev_image)])

    outline = _outline(page_number=2, location_id="rooftop", character_ids=["c0"])
    refs = build_refs(state, outline, max_refs=2, include_prev=True)
    assert len(refs) == 2
    labels = [r.label for r in refs]
    assert labels[0] == "スタイル参照画"
    assert "場所" in labels[1]
    assert all(not r.label.startswith("直前ページ") for r in refs)


def test_refs_returns_labeled_ref_type(tmp_path: Path) -> None:
    state = _state(tmp_path, n_characters=1)
    outline = _outline(page_number=1, location_id="rooftop", character_ids=["c0"])
    refs = build_refs(state, outline, max_refs=16, include_prev=False)
    for r in refs:
        assert isinstance(r, LabeledRef)
        assert isinstance(r.path, Path)
        assert r.label
