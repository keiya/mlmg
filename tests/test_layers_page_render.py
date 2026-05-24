"""Tests for the parallel page_render layer (commit (d) of the
parallel-image-generation refactor).

Covers:
- All-pages-success: every page lands on disk + checkpoint reflects it.
- Resume: a partially-rendered run picks up only the missing pages
  (existing PNG files don't get re-written; save_bytes_strict would
  fail loud if they did).
- Partial failure with drain: one page errors mid-batch; the executor
  drains in-flight successes through on_complete before returning
  Failure, so the next resume sees fewer remaining pages.
- Worker count=1 produces the same outcome as worker count=4 (parity
  with the old serial behavior, modulo file-on-disk ordering).
- Missing prerequisites (no page_plan / no stylist) fail at entry.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from _helpers import make_test_config, prompts_dir
from returns.result import Failure, Success

from mangaka.config import ConcurrencyConfig, MangakaConfig
from mangaka.domain import (
    MPBV,
    ArcPhase,
    Backstories,
    Character,
    Location,
    MangaState,
    MasterPlot,
    Page,
    PageOutline,
    PagePlan,
    Stylist,
)
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.client_fake import FakeImageClient
from mangaka.layers.page_render import generate_page_render_layer
from mangaka.llm.client_fake import FakeLLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.persistence import load_state, state_path_for

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_state(tmp_path: Path, *, n_pages: int = 4) -> MangaState:
    """Build a MangaState wired through every upstream layer so
    page_render's prerequisites are satisfied."""
    style_ref = tmp_path / "assets" / "style.png"
    style_ref.parent.mkdir(parents=True, exist_ok=True)
    style_ref.write_bytes(b"stylebytes")

    char_sheet = tmp_path / "assets" / "characters" / "alice.png"
    char_sheet.parent.mkdir(parents=True, exist_ok=True)
    char_sheet.write_bytes(b"alicebytes")

    loc_sheet = tmp_path / "assets" / "locations" / "rooftop.png"
    loc_sheet.parent.mkdir(parents=True, exist_ok=True)
    loc_sheet.write_bytes(b"rooftopbytes")

    return MangaState(
        seed_input="seed",
        run_name="run",
        master_plot=MasterPlot(raw_markdown="# plot"),
        backstories=Backstories(raw_markdown="# bs"),
        mpbv=MPBV(raw_markdown="# mpbv §1 全体像\n要約\n## §2 ビート\n…"),
        stylist=Stylist(raw_markdown="# style", style_ref_path=style_ref),
        characters=[
            Character(id="alice", name="アリス", description="d", sheet_paths=[char_sheet])
        ],
        locations=[
            Location(id="rooftop", name="屋上", description="d", sheet_path=loc_sheet)
        ],
        page_plan=PagePlan(
            total_pages=n_pages,
            arc=[ArcPhase(phase="起", start_page=1, end_page=n_pages, summary="x")],
            page_outline=[
                PageOutline(
                    page_number=i + 1,
                    phase="起",
                    summary=f"ページ{i + 1}の出来事",
                    character_ids=["alice"],
                    location_id="rooftop",
                )
                for i in range(n_pages)
            ],
        ),
    )


def _config_with_workers(workers: int) -> MangakaConfig:
    # MangakaConfig is a Pydantic BaseModel; use `model_copy` not dataclass replace.
    return make_test_config().model_copy(
        update={"concurrency": ConcurrencyConfig(image_workers=workers)}
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workers", [1, 4, 8])
def test_renders_all_pages_in_parallel(tmp_path: Path, workers: int) -> None:
    """Every page in the outline lands on disk; state reflects all paths."""
    state = _make_state(tmp_path, n_pages=4)
    img = FakeImageClient(default_bytes=b"PNGBYTES")
    result = generate_page_render_layer(
        state,
        FakeLLMClient(default_response=""),
        img,
        _config_with_workers(workers),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    s = result.unwrap()
    assert len(s.pages) == 4
    assert all(p.image_path is not None for p in s.pages)
    for p in s.pages:
        assert p.image_path is not None
        assert p.image_path == tmp_path / "pages" / f"page_{p.page_number:03d}.png"
        assert p.image_path.read_bytes() == b"PNGBYTES"
    # Exactly 4 image API calls; no speculative extras.
    assert len(img.calls) == 4


def test_resume_skips_already_rendered_pages(tmp_path: Path) -> None:
    """Pages with image_path set in state are not re-rendered. Existing
    PNG files on disk would block strict save anyway — verify we don't
    even attempt them."""
    state = _make_state(tmp_path, n_pages=4)
    # Pre-render pages 1 and 2 (state + file on disk).
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2):
        path = pages_dir / f"page_{n:03d}.png"
        path.write_bytes(b"previously rendered")
    state = replace(
        state,
        pages=[
            Page(page_number=1, image_path=pages_dir / "page_001.png"),
            Page(page_number=2, image_path=pages_dir / "page_002.png"),
        ],
    )

    img = FakeImageClient(default_bytes=b"FRESH")
    result = generate_page_render_layer(
        state,
        FakeLLMClient(default_response=""),
        img,
        _config_with_workers(4),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    s = result.unwrap()
    # Only pages 3 & 4 hit the API.
    assert len(img.calls) == 2
    # Original files untouched (strict save would have raised otherwise).
    assert (pages_dir / "page_001.png").read_bytes() == b"previously rendered"
    assert (pages_dir / "page_002.png").read_bytes() == b"previously rendered"
    # New pages got the fresh bytes.
    assert (pages_dir / "page_003.png").read_bytes() == b"FRESH"
    assert (pages_dir / "page_004.png").read_bytes() == b"FRESH"
    # Every page in state has a path.
    assert all(p.image_path is not None for p in s.pages)


def test_no_jobs_when_all_pages_already_rendered(tmp_path: Path) -> None:
    state = _make_state(tmp_path, n_pages=2)
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2):
        (pages_dir / f"page_{n:03d}.png").write_bytes(b"done")
    state = replace(
        state,
        pages=[
            Page(page_number=n, image_path=pages_dir / f"page_{n:03d}.png")
            for n in (1, 2)
        ],
    )

    img = FakeImageClient()
    result = generate_page_render_layer(
        state,
        FakeLLMClient(default_response=""),
        img,
        _config_with_workers(4),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    assert len(img.calls) == 0


# ---------------------------------------------------------------------------
# Failure + drain
# ---------------------------------------------------------------------------


def test_partial_failure_drains_completed_pages_to_state(tmp_path: Path) -> None:
    """One page errors; the drain protocol ensures every in-flight
    success has its on_complete called → the checkpoint reflects them
    even on Failure. The failed page itself is NEVER in the checkpoint.

    Pinned to `workers=1` so the FakeImageClient results sequence maps
    deterministically to page_number order (pop(0) is not thread-safe).
    The exact set of rendered pages depends on whether page 4's pending
    future is picked up by the worker before cancel reaches it — both
    {1,2} and {1,2,4} are valid drain outcomes. What we assert is the
    invariant: page 3 (the failed one) is never persisted as rendered,
    and at least the pre-failure pages (1, 2) made it through drain.
    """
    state = _make_state(tmp_path, n_pages=4)
    err = MangaError(kind=ErrorKind.IMAGE_CALL_FAILED, message="forced render fail")
    img = FakeImageClient(
        results=[Success(b"PNG1"), Success(b"PNG2"), Failure(err), Success(b"PNG4")]
    )
    result = generate_page_render_layer(
        state,
        FakeLLMClient(default_response=""),
        img,
        _config_with_workers(1),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().message == "forced render fail"

    checkpoint = load_state(state_path_for(tmp_path, "page_render"))
    assert isinstance(checkpoint, Success)
    ckpt = checkpoint.unwrap()
    rendered_numbers = {
        p.page_number for p in ckpt.pages if p.image_path is not None
    }
    # Plan §3.6 invariant: failed page is never persisted as rendered.
    assert 3 not in rendered_numbers
    # Drain: pre-failure successes (1 and 2) must be in the checkpoint.
    assert {1, 2}.issubset(rendered_numbers)


def test_missing_page_plan_fails_at_entry(tmp_path: Path) -> None:
    state = _make_state(tmp_path, n_pages=2)
    state = replace(state, page_plan=None)
    result = generate_page_render_layer(
        state,
        FakeLLMClient(default_response=""),
        FakeImageClient(),
        _config_with_workers(4),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_missing_stylist_fails_at_entry(tmp_path: Path) -> None:
    state = _make_state(tmp_path, n_pages=2)
    state = replace(state, stylist=None)
    result = generate_page_render_layer(
        state,
        FakeLLMClient(default_response=""),
        FakeImageClient(),
        _config_with_workers(4),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE
