"""Tests for `build_page_prompt` and `extract_visual_summary`."""

from __future__ import annotations

import textwrap
from dataclasses import replace
from pathlib import Path

from _helpers import make_test_config
from returns.result import Failure, Success

from mangaka.domain import (
    MPBV,
    ArcPhase,
    Character,
    Location,
    MangaState,
    PageOutline,
    PagePlan,
    Stylist,
)
from mangaka.errors import ErrorKind
from mangaka.image.prompts import build_page_prompt, extract_visual_summary
from mangaka.image.ref_builder import LabeledRef

_STYLE_GUIDE = textwrap.dedent(
    """\
    # Style Guide

    ## 4. 全体の絵柄方針
    線画ベース

    ## 5. 線
    強弱あり

    ## 6. 配色とトーン
    寒色寄り

    ## 9. コマと演出
    フキダシ多め

    ## 10. 禁止事項
    写実シェーディング禁止
    """
)

_MPBV_TEXT = textwrap.dedent(
    """\
    ## 1. 基本情報 (Basic Information)
    * ログライン: 静かな決意の物語。
    * コアテーマ: 自分の選択を信じる。
    * 異常性: 屋上に見える景色だけが時間ごとに違って見える。

    ## 2. 世界観の概要 (World Setting Summary)
    * 舞台: 高校の屋上。
    * 重要なルール: 朝に立てた決意は夕方には鈍くなる。
    """
)


def _state(tmp_path: Path) -> MangaState:
    style_ref = tmp_path / "style.png"
    style_ref.write_bytes(b"x")
    page_plan = PagePlan(
        total_pages=2,
        arc=[
            ArcPhase(phase="起", start_page=1, end_page=1, summary="アリスの決意"),
            ArcPhase(phase="承", start_page=2, end_page=2, summary="ボブの登場"),
        ],
        page_outline=[
            PageOutline(
                page_number=1,
                phase="起",
                summary="アリスが屋上で街を見下ろし、今日こそと静かに決意する",
                character_ids=["alice", "bob"],
                location_id="rooftop",
            ),
            PageOutline(
                page_number=2,
                phase="承",
                summary="ボブが現れて声をかける",
                character_ids=["alice", "bob"],
                location_id="rooftop",
            ),
        ],
    )
    return MangaState(
        seed_input="s",
        run_name="r",
        mpbv=MPBV(raw_markdown=_MPBV_TEXT),
        stylist=Stylist(raw_markdown=_STYLE_GUIDE, style_ref_path=style_ref),
        characters=[
            Character(
                id="alice",
                name="アリス",
                description=textwrap.dedent(
                    """\
                    ## アリス (alice)

                    ### 外見 (Visual Identity)
                    - 髪型: ボブ
                    - 目: 大きく丸い
                    """
                ),
                sheet_paths=[tmp_path / "alice.png"],
            ),
            Character(
                id="bob",
                name="ボブ",
                description="(no structured description)",
                sheet_paths=[tmp_path / "bob.png"],
            ),
        ],
        locations=[
            Location(
                id="rooftop",
                name="屋上",
                description=textwrap.dedent(
                    """\
                    ## 屋上 (rooftop)

                    ### 視覚的特徴
                    - 全体: 明るい
                    - 色味: 薄青
                    """
                ),
                sheet_path=tmp_path / "rooftop.png",
            ),
        ],
        page_plan=page_plan,
    )


def _outline(state: MangaState, page_number: int = 1) -> PageOutline:
    assert state.page_plan is not None
    return next(
        o for o in state.page_plan.page_outline if o.page_number == page_number
    )


def _refs(state: MangaState) -> list[LabeledRef]:
    assert state.stylist is not None
    return [
        LabeledRef(path=state.stylist.style_ref_path, label="スタイル参照画"),
        LabeledRef(path=state.locations[0].sheet_path, label="場所「屋上」の設定画"),
        LabeledRef(
            path=state.characters[0].sheet_paths[0],
            label="登場キャラ「アリス」の設定画",
        ),
    ]


# ---------------------------------------------------------------------------
# extract_visual_summary
# ---------------------------------------------------------------------------


def test_extract_visual_summary_pulls_visual_identity_subsection() -> None:
    md = textwrap.dedent(
        """\
        ## アリス (alice)

        ### 基本情報
        - 年齢: 17

        ### 外見 (Visual Identity)
        - 髪型: ボブ
        """
    )
    out = extract_visual_summary(md, max_chars=200)
    assert "ボブ" in out
    assert "年齢" not in out  # only the 外見 subsection


def test_extract_visual_summary_falls_back_to_location_visual_section() -> None:
    md = textwrap.dedent(
        """\
        ## 屋上

        ### 視覚的特徴
        - 明るい
        """
    )
    out = extract_visual_summary(md, max_chars=200)
    assert "明るい" in out


def test_extract_visual_summary_truncates_with_ellipsis() -> None:
    md = "### 外見\n" + "あ" * 500
    out = extract_visual_summary(md, max_chars=20)
    assert len(out) <= 21  # 20 chars + ellipsis
    assert out.endswith("…")


def test_extract_visual_summary_no_subsection_uses_whole_body() -> None:
    out = extract_visual_summary("just a paragraph, no headers", max_chars=200)
    assert "paragraph" in out


# ---------------------------------------------------------------------------
# build_page_prompt
# ---------------------------------------------------------------------------


def test_build_page_prompt_happy_path(tmp_path: Path) -> None:
    state = _state(tmp_path)
    config = make_test_config()
    result = build_page_prompt(state, _outline(state), _refs(state), config)
    assert isinstance(result, Success)
    prompt = result.unwrap()
    # Top-level header
    assert "縦長の漫画ページ" in prompt
    # MPBV overview (story-level context — added PoC 2026-05-24)
    assert "【物語の全貌】" in prompt
    assert "ログライン" in prompt
    assert "異常性" in prompt
    # Arc position
    assert "【このページの位置】" in prompt
    assert "1 ページ目" in prompt
    assert "phase「起」" in prompt
    # The per-page beat summary — the semantic core
    assert "【このページの骨格】" in prompt
    assert "アリスが屋上で街を見下ろし" in prompt
    # Location + characters
    assert "【場所】" in prompt
    assert "明るい" in prompt
    assert "【登場人物】" in prompt
    assert "アリス" in prompt
    # Refs in numbered order
    assert "【参照画像の構成】" in prompt
    assert "1 枚目: スタイル参照画" in prompt
    # Style guidance — stylist sections 4/5/6/9 only, not §10 禁止事項
    assert "【絵柄と演出】" in prompt
    assert "禁止事項" not in prompt
    assert "写実シェーディング禁止" not in prompt
    # Craft direction handed to the model
    assert "【あなたが決めること】" in prompt
    assert "ナレーション枠" in prompt
    # Text rules
    assert "【文字について】" in prompt


def test_build_page_prompt_fails_when_too_long(tmp_path: Path) -> None:
    state = _state(tmp_path)
    config = make_test_config()
    # Forge a page outline with absurdly long summary to blow the budget.
    huge_summary = "あ" * 30000
    outline = _outline(state)
    outline_long = replace(outline, summary=huge_summary)
    result = build_page_prompt(state, outline_long, _refs(state), config)
    assert isinstance(result, Failure)
    err = result.failure()
    assert err.kind == ErrorKind.PROMPT_TOO_LONG
    assert err.detail is not None
    assert "chars" in err.detail


def test_build_page_prompt_requires_stylist(tmp_path: Path) -> None:
    state = MangaState(seed_input="s", run_name="r")  # no stylist / mpbv / page_plan
    page_plan = PagePlan(
        total_pages=1,
        arc=[ArcPhase(phase="起", start_page=1, end_page=1, summary="x")],
        page_outline=[
            PageOutline(
                page_number=1, phase="起", summary="x",
                character_ids=[], location_id="x",
            )
        ],
    )
    state = replace(state, page_plan=page_plan, mpbv=MPBV(raw_markdown=""))
    result = build_page_prompt(state, page_plan.page_outline[0], [], make_test_config())
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_build_page_prompt_character_total_budget_truncates(tmp_path: Path) -> None:
    """The character block stops appending when total_max would be exceeded.

    Regression guard for the M3-era fix where the loop accumulator wasn't
    enforced — instead of just lowering per-char-max, the total max can
    cut off later (lower-priority) characters entirely.
    """
    state = _state(tmp_path)
    config = make_test_config()
    config = config.model_copy(
        update={
            "image": config.image.model_copy(
                update={"max_character_summary_total_chars": 30}
            )
        }
    )
    result = build_page_prompt(state, _outline(state), _refs(state), config)
    assert isinstance(result, Success)
    out = result.unwrap()
    # alice is character_ids[0] (priority order) and survives within 30 chars.
    assert "アリス" in out
    # ボブ is lower priority; under the tight 30-char budget there's no room
    # for a second `- name: ...` line — the loop must have broken early.
    char_block = out.split("【参照画像の構成】")[0].split("【登場人物】")[1]
    char_lines = [
        line for line in char_block.splitlines() if line.startswith("- ボブ:")
    ]
    assert char_lines == [], "ボブ should have been dropped by the total budget"
