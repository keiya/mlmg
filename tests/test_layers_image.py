"""Image-layer tests for Stylist / Character / Location.

All wired through `FakeLLMClient` + `FakeImageClient` — no real APIs hit.
"""

from __future__ import annotations

import textwrap
from dataclasses import replace
from pathlib import Path

from _helpers import make_test_config, prompts_dir
from returns.result import Failure, Success

from mangaka.domain import MPBV, Backstories, Character, Location, MangaState, MasterPlot, Stylist
from mangaka.errors import ErrorKind
from mangaka.image.client_fake import FakeImageClient
from mangaka.layers.character import generate_character_layer
from mangaka.layers.location import generate_location_layer
from mangaka.layers.stylist import generate_stylist_layer
from mangaka.llm.client_fake import FakeLLMClient
from mangaka.llm.prompts import PromptLoader

_STYLIST_RESPONSE = textwrap.dedent(
    """\
    # Style Guide

    ## 1. ジャンル・トーン
    シリアス少年漫画。

    ## 2. テンポと演出密度
    高密度。

    ## 3. セリフとモノローグの傾向
    モノローグ多。

    ## 4. 全体の絵柄方針
    線画ベース、コントラスト強。

    ## 5. 線
    強弱あるデジタル線。

    ## 6. 配色とトーン
    寒色寄り。

    ## 7. キャラデザインの傾向
    瞳大きめ。

    ## 8. 背景と空間
    密度高。

    ## 9. コマと演出
    フキダシ多め。

    ## 10. 禁止事項
    写実シェーディング禁止。
    """
)

_CHARACTER_RESPONSE = textwrap.dedent(
    """\
    ## アリス (alice)

    ### 基本情報
    - 年齢: 17

    ### 外見 (Visual Identity)
    - 髪型・髪色: ボブカット、黒
    - 目: 大きく丸い

    ### 性格と口調
    元気

    ### 物語上の役割
    主人公

    ## ボブ (bob)

    ### 基本情報
    - 年齢: 17

    ### 外見 (Visual Identity)
    - 髪型・髪色: 坊主、茶
    - 目: 細め

    ### 性格と口調
    寡黙

    ### 物語上の役割
    相方
    """
)

_LOCATION_RESPONSE = textwrap.dedent(
    """\
    ## 屋上 朝 (rooftop_morning)

    ### 基本情報
    - 種別: 屋外
    - 時間帯: 朝

    ### 視覚的特徴
    - 全体: 明るい
    - 色味: 薄青

    ### 物語上の使われ方
    告白シーン
    """
)


def _seed_state() -> MangaState:
    return MangaState(
        seed_input="seed",
        run_name="t",
        master_plot=MasterPlot(raw_markdown="# plot"),
        backstories=Backstories(raw_markdown="# bs"),
        mpbv=MPBV(raw_markdown="# mpbv"),
    )


# ---------------------------------------------------------------------------
# Stylist
# ---------------------------------------------------------------------------


def test_stylist_layer_produces_text_and_image(tmp_path: Path) -> None:
    state = _seed_state()
    llm = FakeLLMClient(default_response=_STYLIST_RESPONSE)
    img = FakeImageClient()
    config = make_test_config(runs_dir=str(tmp_path))
    loader = PromptLoader(prompts_dir())

    result = generate_stylist_layer(state, llm, img, config, loader, run_dir=tmp_path)
    assert isinstance(result, Success)

    s = result.unwrap()
    assert s.stylist is not None
    assert s.stylist.raw_markdown == _STYLIST_RESPONSE
    assert s.stylist.style_ref_path == tmp_path / "assets" / "style.png"
    assert s.stylist.style_ref_path.read_bytes() == img.default_bytes

    # Image prompt should contain only the style_ref section subset.
    image_call = img.calls[0]
    assert image_call.method == "generate"
    assert "## 4. 全体の絵柄方針" in image_call.prompt
    assert "## 10. 禁止事項" in image_call.prompt
    assert "## 1. ジャンル・トーン" not in image_call.prompt


def test_stylist_layer_requires_mpbv(tmp_path: Path) -> None:
    state = MangaState(seed_input="s", run_name="t")  # no mpbv
    result = generate_stylist_layer(
        state,
        FakeLLMClient(),
        FakeImageClient(),
        make_test_config(),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_stylist_layer_failure_when_required_sections_missing(tmp_path: Path) -> None:
    state = _seed_state()
    llm = FakeLLMClient(default_response="No sections here, just text.")
    result = generate_stylist_layer(
        state,
        llm,
        FakeImageClient(),
        make_test_config(),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR


def test_stylist_layer_failure_when_any_section_missing(tmp_path: Path) -> None:
    """Regression guard for M2 round-2 P2 fix.

    The stylist guide must contain ALL 10 sections, not just the style_ref
    subset. Section 7 (character design) is consumed by character layer,
    section 8 (background) by location layer — if either is missing, the
    downstream `extract_sections` silently produces empty guidance.
    """
    # Sections 4/5/6/10 are present (style_ref subset OK) but 7 is missing —
    # would have passed the round-1 check but must now fail the round-2 check.
    partial = textwrap.dedent(
        """\
        # Style Guide

        ## 1. ジャンル・トーン
        シリアス

        ## 2. テンポと演出密度
        高密度

        ## 3. セリフとモノローグの傾向
        モノローグ多

        ## 4. 全体の絵柄方針
        線画ベース

        ## 5. 線
        強弱あり

        ## 6. 配色とトーン
        寒色寄り

        ## 8. 背景と空間
        密度高

        ## 9. コマと演出
        フキダシ多

        ## 10. 禁止事項
        写実シェーディング禁止
        """
    )  # section 7 deliberately missing
    state = _seed_state()
    result = generate_stylist_layer(
        state,
        FakeLLMClient(default_response=partial),
        FakeImageClient(),
        make_test_config(),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    err = result.failure()
    assert err.kind == ErrorKind.PARSE_ERROR
    assert err.detail is not None
    missing = err.detail.get("missing_sections")
    assert isinstance(missing, list)
    assert 7 in missing


def test_stylist_versioned_save_when_existing_file(tmp_path: Path) -> None:
    """If style.png already exists, the layer must save to style_v002.png."""
    state = _seed_state()
    pre = tmp_path / "assets" / "style.png"
    pre.parent.mkdir(parents=True)
    pre.write_bytes(b"stale-from-previous-run")

    img = FakeImageClient(default_bytes=b"fresh")
    result = generate_stylist_layer(
        state,
        FakeLLMClient(default_response=_STYLIST_RESPONSE),
        img,
        make_test_config(),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    s = result.unwrap()
    assert s.stylist is not None
    assert s.stylist.style_ref_path.name == "style_v002.png"
    assert pre.read_bytes() == b"stale-from-previous-run"  # untouched


# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------


def _with_stylist(state: MangaState, tmp_path: Path) -> MangaState:
    style_ref = tmp_path / "assets" / "style.png"
    style_ref.parent.mkdir(parents=True, exist_ok=True)
    style_ref.write_bytes(b"stylebytes")
    return replace(
        state,
        stylist=Stylist(raw_markdown=_STYLIST_RESPONSE, style_ref_path=style_ref),
    )


def test_character_layer_generates_per_character_sheet(tmp_path: Path) -> None:
    state = _with_stylist(_seed_state(), tmp_path)
    llm = FakeLLMClient(default_response=_CHARACTER_RESPONSE)
    img = FakeImageClient()
    result = generate_character_layer(
        state, llm, img, make_test_config(), PromptLoader(prompts_dir()), run_dir=tmp_path
    )
    assert isinstance(result, Success)
    s = result.unwrap()
    # Canonical (parsed_chars) order, not completion order.
    assert [c.id for c in s.characters] == ["alice", "bob"]
    assert s.characters[0].sheet_paths[0] == tmp_path / "assets" / "characters" / "alice.png"
    assert s.characters[1].sheet_paths[0] == tmp_path / "assets" / "characters" / "bob.png"
    # Each character got one ImageClient.edit call passing style_ref as a ref.
    assert len(img.calls) == 2
    for call in img.calls:
        assert call.method == "edit"
        assert call.refs == (state.stylist.style_ref_path,)  # type: ignore[union-attr]
    # plan §3.8: raw LLM markdown is cached in state for resume.
    assert s.character_markdown == _CHARACTER_RESPONSE


def test_character_layer_resumes_from_cached_markdown(tmp_path: Path) -> None:
    """plan §3.8: on re-entry with cached markdown, LLM is NOT called
    again. Re-parse the cached text, skip already-done characters,
    render only the missing ones."""
    state = _with_stylist(_seed_state(), tmp_path)
    # Simulate prior partial run: markdown cached, alice done, bob missing.
    alice_sheet = tmp_path / "assets" / "characters" / "alice.png"
    alice_sheet.parent.mkdir(parents=True, exist_ok=True)
    alice_sheet.write_bytes(b"alice_from_prior_run")
    state = replace(
        state,
        character_markdown=_CHARACTER_RESPONSE,
        characters=[
            Character(
                id="alice", name="アリス", description="d",
                sheet_paths=[alice_sheet],
            )
        ],
    )
    # FakeLLMClient with no default would error if called — proves we don't.
    llm = FakeLLMClient(default_response="DO_NOT_CALL_ME")
    img = FakeImageClient(default_bytes=b"bob_fresh")
    result = generate_character_layer(
        state, llm, img, make_test_config(), PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    s = result.unwrap()
    # LLM was NOT called (cached markdown reused).
    assert len(llm.calls) == 0
    # Only bob hit the image API (alice was already done).
    assert len(img.calls) == 1
    # State has both characters, alice's bytes untouched.
    assert {c.id for c in s.characters} == {"alice", "bob"}
    assert alice_sheet.read_bytes() == b"alice_from_prior_run"


def test_character_layer_requires_stylist(tmp_path: Path) -> None:
    state = _seed_state()
    result = generate_character_layer(
        state,
        FakeLLMClient(default_response=_CHARACTER_RESPONSE),
        FakeImageClient(),
        make_test_config(),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_character_layer_over_limit_warns_but_continues(tmp_path: Path) -> None:
    """PoC 2026-05-24: relaxed from fatal to warn-only. MPBV often inflates
    the cast with voice/concept entities, and the run-killing strict cap
    hurt more than it helped (user-facing impact > the $0.21 per-char cost
    the cap was meant to defend). Now the layer logs a warning and generates
    all proposed characters.
    """
    state = _with_stylist(_seed_state(), tmp_path)
    too_many = "\n\n".join(
        f"## c{i} (c{i})\n### 外見\n描写{i}" for i in range(20)
    )
    llm = FakeLLMClient(default_response=too_many)
    result = generate_character_layer(
        state, llm, FakeImageClient(), make_test_config(),
        PromptLoader(prompts_dir()), run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    new_state = result.unwrap()
    assert len(new_state.characters) == 20


def test_character_layer_preflights_visual_sections_before_rendering(
    tmp_path: Path,
) -> None:
    """Round-6 review fix: malformed late character must not burn paid image
    calls on earlier blocks. Pre-flight rejects the whole batch before any
    `img.edit` runs.
    """
    state = _with_stylist(_seed_state(), tmp_path)
    # alice has 外見, bob does NOT — old behavior: alice's sheet generated +
    # saved, bob's parse fails. New behavior: zero image calls, atomic fail.
    md = textwrap.dedent(
        """\
        ## アリス (alice)
        ### 外見
        ボブカット

        ## ボブ (bob)
        ### 性格と口調
        寡黙
        """
    )
    img = FakeImageClient()
    result = generate_character_layer(
        state,
        FakeLLMClient(default_response=md),
        img,
        make_test_config(),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR
    assert len(img.calls) == 0  # NO paid image work before parse validates
    assert not (tmp_path / "assets" / "characters" / "alice.png").exists()
    # plan §3.8 invariant: even though the layer failed, the raw LLM
    # markdown was persisted to disk BEFORE the preflight ran, so resume
    # can reuse it without paying for another LLM call.
    state_file = tmp_path / "state_05_character.json"
    assert state_file.exists()
    from mangaka.persistence import load_state

    loaded = load_state(state_file)
    assert isinstance(loaded, Success)
    assert loaded.unwrap().character_markdown == md


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


def test_location_layer_generates_sheet(tmp_path: Path) -> None:
    state = _with_stylist(_seed_state(), tmp_path)
    llm = FakeLLMClient(default_response=_LOCATION_RESPONSE)
    img = FakeImageClient()
    result = generate_location_layer(
        state, llm, img, make_test_config(), PromptLoader(prompts_dir()), run_dir=tmp_path
    )
    assert isinstance(result, Success)
    s = result.unwrap()
    assert [loc.id for loc in s.locations] == ["rooftop_morning"]
    assert s.locations[0].sheet_path == tmp_path / "assets" / "locations" / "rooftop_morning.png"
    assert len(img.calls) == 1
    assert img.calls[0].method == "edit"
    # plan §3.8: raw LLM markdown is cached in state for resume.
    assert s.location_markdown == _LOCATION_RESPONSE


def test_location_layer_resumes_from_cached_markdown(tmp_path: Path) -> None:
    """Symmetric to test_character_layer_resumes_from_cached_markdown."""
    state = _with_stylist(_seed_state(), tmp_path)
    loc_sheet = tmp_path / "assets" / "locations" / "rooftop_morning.png"
    loc_sheet.parent.mkdir(parents=True, exist_ok=True)
    loc_sheet.write_bytes(b"loc_from_prior_run")
    state = replace(
        state,
        location_markdown=_LOCATION_RESPONSE,
        locations=[
            Location(
                id="rooftop_morning", name="屋上 朝",
                description="d", sheet_path=loc_sheet,
            )
        ],
    )
    llm = FakeLLMClient(default_response="DO_NOT_CALL_ME")
    img = FakeImageClient()
    result = generate_location_layer(
        state, llm, img, make_test_config(), PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Success)
    s = result.unwrap()
    assert len(llm.calls) == 0
    # Already-done location → no image work.
    assert len(img.calls) == 0
    assert len(s.locations) == 1
    assert loc_sheet.read_bytes() == b"loc_from_prior_run"


def test_location_layer_preflights_visual_sections_before_rendering(
    tmp_path: Path,
) -> None:
    """Round-6 review fix (symmetric to character preflight)."""
    state = _with_stylist(_seed_state(), tmp_path)
    md = textwrap.dedent(
        """\
        ## 屋上 朝 (rooftop_morning)
        ### 視覚的特徴
        明るい

        ## 教室 (classroom)
        ### 基本情報
        屋内
        """
    )  # second location missing 視覚的特徴
    img = FakeImageClient()
    result = generate_location_layer(
        state,
        FakeLLMClient(default_response=md),
        img,
        make_test_config(),
        PromptLoader(prompts_dir()),
        run_dir=tmp_path,
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR
    assert len(img.calls) == 0
    assert not (tmp_path / "assets" / "locations" / "rooftop_morning.png").exists()
