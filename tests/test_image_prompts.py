"""Tests for `build_page_prompt` and `extract_visual_summary`."""

from __future__ import annotations

import textwrap
from dataclasses import replace
from pathlib import Path

from _helpers import make_test_config
from returns.result import Failure, Success

from mangaka.domain import (
    SFX,
    Character,
    Location,
    MangaState,
    PageBeat,
    Panel,
    SpeechIntent,
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


def _state(tmp_path: Path) -> MangaState:
    style_ref = tmp_path / "style.png"
    style_ref.write_bytes(b"x")
    return MangaState(
        seed_input="s",
        run_name="r",
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
    )


def _beat(page_number: int = 1, character_ids: list[str] | None = None) -> PageBeat:
    return PageBeat(
        page_number=page_number,
        phase="起",
        location_id="rooftop",
        character_ids=character_ids or ["alice", "bob"],
        mood="静かな緊張",
        continuity_note="前ページから時間連続",
        panels=[
            Panel(
                panel_no=1,
                size_hint="large",
                visual="アリスが街を見下ろす",
                emotion="決意",
                camera="ミドルショット",
                speech_intents=[
                    SpeechIntent(
                        speaker_id="alice",
                        bubble_type="inner_monologue",
                        text="やる、絶対に。",
                        register="静か",
                    ),
                ],
                sfx=[SFX(text="ヒュウ", role="風")],
            ),
            Panel(
                panel_no=2,
                size_hint="regular",
                visual="ボブが現れる",
                emotion="意外",
                camera=None,
                speech_intents=[
                    SpeechIntent(
                        speaker_id="narrator",
                        bubble_type="narration",
                        text="夜明け前——。",
                        register=None,
                    ),
                ],
                sfx=[],
            ),
        ],
        md_path=Path("page_beat_001.md"),
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
    result = build_page_prompt(state, _beat(), _refs(state), config)
    assert isinstance(result, Success)
    prompt = result.unwrap()
    # Spot-check expected sections.
    assert "縦長の漫画ページ" in prompt
    assert "【場所】" in prompt
    assert "屋上" not in prompt or "明るい" in prompt  # loc summary present
    assert "【登場人物】" in prompt
    assert "アリス" in prompt
    assert "【このページの空気】" in prompt
    assert "静かな緊張" in prompt
    assert "【コマ構成】" in prompt
    assert "コマ 1" in prompt
    assert "■ コマ 2" in prompt
    assert "ヒュウ" in prompt  # SFX surfaced
    assert "【参照画像の構成】" in prompt
    assert "1 枚目: スタイル参照画" in prompt
    assert "【絵柄と演出】" in prompt
    assert "【文字について】" in prompt
    # 禁止事項 (section 10) is excluded from the page_render section set —
    # it added noise without helping image quality during the PoC.
    assert "禁止事項" not in prompt
    assert "写実シェーディング禁止" not in prompt
    # Page-render-side 【避けること】 was dropped at the same time.
    assert "【避けること】" not in prompt


def test_build_page_prompt_narrator_renders_as_japanese_label(tmp_path: Path) -> None:
    state = _state(tmp_path)
    config = make_test_config()
    result = build_page_prompt(state, _beat(), _refs(state), config)
    assert isinstance(result, Success)
    assert "ナレーション" in result.unwrap()


def test_build_page_prompt_fails_when_too_long(tmp_path: Path) -> None:
    state = _state(tmp_path)
    config = make_test_config()
    # Forge a panel with absurdly long visual to blow the budget.
    huge_visual = "あ" * 30000
    beat = _beat()
    beat = replace(
        beat,
        panels=[
            replace(beat.panels[0], visual=huge_visual),
        ],
    )
    result = build_page_prompt(state, beat, _refs(state), config)
    assert isinstance(result, Failure)
    err = result.failure()
    assert err.kind == ErrorKind.PROMPT_TOO_LONG
    assert err.detail is not None
    assert "chars" in err.detail


def test_build_page_prompt_requires_stylist(tmp_path: Path) -> None:
    state = MangaState(seed_input="s", run_name="r")  # no stylist
    result = build_page_prompt(state, _beat(), [], make_test_config())
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
    result = build_page_prompt(state, _beat(), _refs(state), config)
    assert isinstance(result, Success)
    out = result.unwrap()
    # alice is character_ids[0] (priority order) and survives within 30 chars.
    assert "アリス" in out
    # ボブ is lower priority; under the tight 30-char budget there's no room
    # for a second `- name: ...` line — the loop must have broken early.
    char_block = out.split("【このページの空気】")[0]
    char_lines = [
        line for line in char_block.splitlines() if line.startswith("- ボブ:")
    ]
    assert char_lines == [], "ボブ should have been dropped by the total budget"
