"""Tests for `generate_page_beat_layer` and `generate_page_render_layer`.

Both run via Fake LLM + Fake image clients; no API calls.
"""

from __future__ import annotations

import textwrap
from dataclasses import replace
from pathlib import Path

from _helpers import make_test_config, prompts_dir
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
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.client_fake import FakeImageClient
from mangaka.layers.page_beat import generate_page_beat_layer
from mangaka.layers.page_render import generate_page_render_layer
from mangaka.llm.client_fake import FakeLLMClient
from mangaka.llm.prompts import PromptLoader

_STYLE_GUIDE_10 = textwrap.dedent(
    """\
    ## 1. ジャンル・トーン
    s

    ## 2. テンポと演出密度
    s

    ## 3. セリフとモノローグの傾向
    s

    ## 4. 全体の絵柄方針
    s

    ## 5. 線
    s

    ## 6. 配色とトーン
    s

    ## 7. キャラデザインの傾向
    s

    ## 8. 背景と空間
    s

    ## 9. コマと演出
    s

    ## 10. 禁止事項
    s
    """
)


def _valid_beat_md(page_number: int, *, phase: str = "起") -> str:
    return textwrap.dedent(
        f"""\
        ---
        page_number: {page_number}
        phase: {phase}
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1 [size: regular]

        **Visual**: アリスが屋上に立つ
        **Emotion**: 静かな決意

        **Speech**:
        - [alice / inner_monologue / 静か] 決意

        **SFX**: なし
        """
    )


def _state(tmp_path: Path, *, n_pages: int = 2) -> MangaState:
    style_ref = tmp_path / "style.png"
    style_ref.write_bytes(b"x")
    return MangaState(
        seed_input="s",
        run_name="r",
        master_plot=MasterPlot(raw_markdown="plot"),
        backstories=Backstories(raw_markdown="bs"),
        mpbv=MPBV(raw_markdown="mpbv"),
        stylist=Stylist(raw_markdown=_STYLE_GUIDE_10, style_ref_path=style_ref),
        characters=[
            Character(
                id="alice",
                name="アリス",
                description="### 外見\nボブ",
                sheet_paths=[tmp_path / "alice.png"],
            ),
        ],
        locations=[
            Location(
                id="rooftop",
                name="屋上",
                description="### 視覚的特徴\n明るい",
                sheet_path=tmp_path / "rooftop.png",
            ),
        ],
        page_plan=PagePlan(
            total_pages=n_pages,
            arc=[ArcPhase(phase="起", start_page=1, end_page=n_pages, summary="x")],
            page_outline=[
                PageOutline(
                    page_number=i,
                    phase="起",
                    summary=f"p{i}",
                    character_ids=["alice"],
                    location_id="rooftop",
                )
                for i in range(1, n_pages + 1)
            ],
        ),
    )


# ---------------------------------------------------------------------------
# page_beat layer
# ---------------------------------------------------------------------------


def test_page_beat_layer_generates_every_page(tmp_path: Path) -> None:
    state = _state(tmp_path, n_pages=3)
    llm = FakeLLMClient(
        responses=[_valid_beat_md(1), _valid_beat_md(2), _valid_beat_md(3)]
    )
    img = FakeImageClient()
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    result = generate_page_beat_layer(state, llm, img, config, loader, run_dir=tmp_path)
    assert isinstance(result, Success)
    new_state = result.unwrap()
    assert [p.page_number for p in new_state.pages] == [1, 2, 3]
    for p in new_state.pages:
        assert p.beat.md_path.exists()
        assert p.image_path is None  # PageRender hasn't run yet


def test_page_beat_layer_requires_page_plan(tmp_path: Path) -> None:
    state = _state(tmp_path, n_pages=1)
    state = replace(state, page_plan=None)
    llm = FakeLLMClient(default_response=_valid_beat_md(1))
    result = generate_page_beat_layer(
        state, llm, FakeImageClient(), make_test_config(),
        PromptLoader(prompts_dir()), run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_page_beat_layer_retries_on_validation_failure(tmp_path: Path) -> None:
    """If page_beat output fails Phase 2 validation, layer retries with feedback."""
    state = _state(tmp_path, n_pages=1)
    # Wrong page_number → Phase 2 rejects; second response is valid.
    bad_md = _valid_beat_md(99)  # expected is 1
    good_md = _valid_beat_md(1)
    llm = FakeLLMClient(responses=[bad_md, good_md])
    config = make_test_config()
    config = config.model_copy(
        update={"limits": config.limits.model_copy(update={"max_parse_retries": 2})}
    )
    result = generate_page_beat_layer(
        state, llm, FakeImageClient(), config,
        PromptLoader(prompts_dir()), run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    assert len(llm.calls) == 2
    assert "検証結果" in llm.calls[1].prompt


# ---------------------------------------------------------------------------
# page_render layer
# ---------------------------------------------------------------------------


def _state_with_pages(tmp_path: Path) -> MangaState:
    """Run page_beat to populate state.pages first."""
    state = _state(tmp_path, n_pages=2)
    llm = FakeLLMClient(responses=[_valid_beat_md(1), _valid_beat_md(2)])
    result = generate_page_beat_layer(
        state, llm, FakeImageClient(), make_test_config(),
        PromptLoader(prompts_dir()), run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    return result.unwrap()


def test_page_render_layer_renders_every_page(tmp_path: Path) -> None:
    state = _state_with_pages(tmp_path)
    img = FakeImageClient()
    result = generate_page_render_layer(
        state, FakeLLMClient(), img, make_test_config(),
        PromptLoader(prompts_dir()), run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    new_state = result.unwrap()
    assert len(img.calls) == 2
    for p in new_state.pages:
        assert p.image_path is not None
        assert p.image_path.exists()


def test_page_render_layer_skips_pages_with_image_path(tmp_path: Path) -> None:
    """If a page already has image_path, don't re-render (resume support)."""
    state = _state_with_pages(tmp_path)
    # Mark page 1 as already-rendered.
    pre_rendered = tmp_path / "pages" / "page_001.png"
    pre_rendered.parent.mkdir(parents=True, exist_ok=True)
    pre_rendered.write_bytes(b"x")
    state = replace(
        state,
        pages=[
            replace(state.pages[0], image_path=pre_rendered),
            state.pages[1],
        ],
    )
    img = FakeImageClient()
    result = generate_page_render_layer(
        state, FakeLLMClient(), img, make_test_config(),
        PromptLoader(prompts_dir()), run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    assert len(img.calls) == 1  # only page 2 re-rendered


def test_page_render_layer_requires_pages(tmp_path: Path) -> None:
    state = MangaState(seed_input="s", run_name="r")
    result = generate_page_render_layer(
        state, FakeLLMClient(), FakeImageClient(), make_test_config(),
        PromptLoader(prompts_dir()), run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_page_render_layer_propagates_image_failure(tmp_path: Path) -> None:
    state = _state_with_pages(tmp_path)
    failing_img = FakeImageClient().with_failure(
        MangaError(kind=ErrorKind.IMAGE_CALL_FAILED, message="boom")
    )
    result = generate_page_render_layer(
        state, FakeLLMClient(), failing_img, make_test_config(),
        PromptLoader(prompts_dir()), run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.IMAGE_CALL_FAILED


def test_page_render_layer_checkpoints_after_each_page(tmp_path: Path) -> None:
    """Round-7 review fix: a mid-loop failure must not throw away already-paid
    renders. The state file should be updated after each successful page so a
    resume picks up where rendering stopped.
    """
    from returns.result import Failure as _Failure
    from returns.result import Success as _Success

    from mangaka.persistence import load_state, state_path_for

    state = _state_with_pages(tmp_path)  # 2 pages

    # Image client that succeeds on call 1, fails on call 2.
    img = FakeImageClient(
        results=[
            _Success(b"page1bytes"),
            _Failure(MangaError(kind=ErrorKind.IMAGE_CALL_FAILED, message="boom2")),
        ]
    )
    result = generate_page_render_layer(
        state, FakeLLMClient(), img, make_test_config(),
        PromptLoader(prompts_dir()), run_dir=tmp_path,
    )
    assert isinstance(result, Failure)

    # state_09_page_render.json should exist and reflect page 1's image_path.
    checkpoint = state_path_for(tmp_path, "page_render")
    assert checkpoint.exists(), "checkpoint must be written even on failure"
    loaded_result = load_state(checkpoint)
    assert isinstance(loaded_result, Success)
    loaded = loaded_result.unwrap()
    by_num = {p.page_number: p for p in loaded.pages}
    assert by_num[1].image_path is not None
    assert by_num[2].image_path is None  # never rendered
