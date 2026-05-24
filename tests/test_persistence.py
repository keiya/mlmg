"""Tests for state save/load round-trip and `latest_state_path`."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from returns.result import Failure, Success

from mangaka.domain import (
    MPBV,
    ArcPhase,
    Backstories,
    Character,
    Location,
    MangaState,
    MasterPlot,
    PageOutline,
    PagePlan,
    Stylist,
)
from mangaka.errors import ErrorKind
from mangaka.persistence import (
    from_json,
    latest_state_path,
    load_state,
    save_state,
    state_path_for,
    to_json,
)


def _seed_state() -> MangaState:
    s = MangaState(seed_input="seed", run_name="r")
    s = replace(s, master_plot=MasterPlot(raw_markdown="# plot"))
    s = replace(s, backstories=Backstories(raw_markdown="# bs"))
    s = replace(s, mpbv=MPBV(raw_markdown="# mpbv"))
    return s


def test_round_trip_text_layers() -> None:
    s = _seed_state()
    s2_result = from_json(to_json(s))
    assert isinstance(s2_result, Success)
    s2 = s2_result.unwrap()
    assert s2.seed_input == s.seed_input
    assert s2.run_name == s.run_name
    assert s2.master_plot is not None
    assert s2.master_plot.raw_markdown == "# plot"
    assert s2.backstories is not None
    assert s2.backstories.raw_markdown == "# bs"
    assert s2.mpbv is not None
    assert s2.mpbv.raw_markdown == "# mpbv"


def test_save_and_load_via_path(tmp_path: Path) -> None:
    s = _seed_state()
    target = tmp_path / "nested" / "state.json"
    save_result = save_state(s, target)
    assert isinstance(save_result, Success)
    assert target.exists()

    loaded = load_state(target)
    assert isinstance(loaded, Success)
    assert loaded.unwrap().seed_input == "seed"


def test_load_missing_file_returns_io_error(tmp_path: Path) -> None:
    result = load_state(tmp_path / "nope.json")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.IO_ERROR


def test_from_json_invalid_json() -> None:
    result = from_json("{ not json")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.JSON_INVALID


def test_state_path_for_uses_canonical_name(tmp_path: Path) -> None:
    p = state_path_for(tmp_path, "mpbv")
    assert p == tmp_path / "state_03_mpbv.json"


def test_state_path_for_unknown_layer_is_programmer_bug(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        state_path_for(tmp_path, "bogus")


def test_latest_state_path_picks_highest_index(tmp_path: Path) -> None:
    (tmp_path / "state_00_init.json").write_text("{}")
    (tmp_path / "state_01_plot.json").write_text("{}")
    (tmp_path / "state_03_mpbv.json").write_text("{}")
    (tmp_path / "state_final.json").write_text("{}")  # must be ignored

    result = latest_state_path(tmp_path)
    assert isinstance(result, Success)
    assert result.unwrap().name == "state_03_mpbv.json"


def test_latest_state_path_no_files(tmp_path: Path) -> None:
    result = latest_state_path(tmp_path)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_latest_state_path_missing_dir(tmp_path: Path) -> None:
    result = latest_state_path(tmp_path / "nope")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.IO_ERROR


def test_state_json_does_not_embed_image_paths(tmp_path: Path) -> None:
    """Sanity: text-only seed-state JSON has no path/base64 fields.

    This guards the no-base64 invariant; the image layers in M2 do store
    file paths, but `_seed_state()` here intentionally only sets the text
    layers so the assertion stands.
    """
    s = _seed_state()
    js = to_json(s)
    assert ".png" not in js
    assert "base64" not in js.lower()


def test_round_trip_with_image_layers_and_page_plan(tmp_path: Path) -> None:
    """M2 + M3 layers serialize and round-trip correctly."""
    style_ref = tmp_path / "style.png"
    char_sheet = tmp_path / "alice.png"
    loc_sheet = tmp_path / "rooftop.png"
    for p in (style_ref, char_sheet, loc_sheet):
        p.write_bytes(b"x")

    state = _seed_state()
    state = replace(
        state,
        stylist=Stylist(raw_markdown="# guide", style_ref_path=style_ref),
        characters=[
            Character(id="alice", name="アリス", description="d", sheet_paths=[char_sheet]),
        ],
        locations=[
            Location(id="rooftop", name="屋上", description="d", sheet_path=loc_sheet),
        ],
        page_plan=PagePlan(
            total_pages=2,
            arc=[ArcPhase(phase="起", start_page=1, end_page=2, summary="x")],
            page_outline=[
                PageOutline(
                    page_number=1, phase="起", summary="p1",
                    character_ids=["alice"], location_id="rooftop",
                ),
                PageOutline(
                    page_number=2, phase="起", summary="p2",
                    character_ids=["alice"], location_id="rooftop",
                ),
            ],
        ),
    )

    rt_result = from_json(to_json(state))
    assert isinstance(rt_result, Success)
    rt = rt_result.unwrap()

    assert rt.stylist is not None
    assert rt.stylist.style_ref_path == style_ref
    assert rt.characters[0].id == "alice"
    assert rt.characters[0].sheet_paths == [char_sheet]
    assert rt.locations[0].id == "rooftop"
    assert rt.page_plan is not None
    assert rt.page_plan.total_pages == 2
    assert rt.page_plan.arc[0].phase == "起"
    assert rt.page_plan.page_outline[1].character_ids == ["alice"]
