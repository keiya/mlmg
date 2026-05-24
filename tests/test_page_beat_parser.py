"""Tests for the PageBeat Markdown + YAML frontmatter parser (Phase 1 + 2)."""

from __future__ import annotations

import textwrap

from returns.result import Failure, Result, Success

from mangaka.errors import ErrorKind, MangaError
from mangaka.parse.page_beat import (
    parse_page_beat_text,
    validate_page_beat,
)

_VALID_MD = textwrap.dedent(
    """\
    ---
    page_number: 5
    phase: 対立
    location_id: rooftop_morning
    character_ids: [alice, bob]
    mood: 緊迫から再会へ
    continuity_note: 前ページから時間連続
    ---

    ## Panel 1 [size: regular]

    **Visual**: アリスが屋上の柵を握りしめる。

    **Camera**: ミドルショット
    **Emotion**: 決意

    **Speech**:
    - [alice / inner_monologue / 静かなモノローグ] 決意する気持ち

    **SFX**:
    - ヒュウ (風)

    ## Panel 2 [size: large]

    **Visual**: ボブが屋上のドアを開ける。

    **Emotion**: 不意の登場

    **Speech**:
    - [bob / dialogue / 落ち着いた声] アリスの名を呼ぶ

    **SFX**: なし
    """
)


# ---------------------------------------------------------------------------
# Phase 1: tolerant parse
# ---------------------------------------------------------------------------


def test_parses_pagebeat_wrapped_in_markdown_fence() -> None:
    """Round-3 review fix: LLMs commonly echo the prompt's example fence.

    Before this fix, `frontmatter.loads` saw no YAML head and validation
    burned the parse retry budget on otherwise-valid content.
    """
    fenced = f"```markdown\n{_VALID_MD}\n```"
    result = parse_page_beat_text(fenced)
    assert isinstance(result, Success)
    assert result.unwrap().frontmatter.page_number == 5


def test_parses_pagebeat_wrapped_in_bare_fence() -> None:
    """Plain ``` ... ``` without a language tag also works."""
    fenced = f"```\n{_VALID_MD}\n```"
    result = parse_page_beat_text(fenced)
    assert isinstance(result, Success)


def test_parses_full_valid_pagebeat() -> None:
    result = parse_page_beat_text(_VALID_MD)
    assert isinstance(result, Success)
    parsed = result.unwrap()
    assert parsed.frontmatter.page_number == 5
    assert parsed.frontmatter.phase == "対立"
    assert parsed.frontmatter.location_id == "rooftop_morning"
    assert parsed.frontmatter.character_ids == ["alice", "bob"]
    assert parsed.frontmatter.continuity_note is not None
    assert len(parsed.panels) == 2
    assert parsed.panels[0].size_hint == "regular"
    assert parsed.panels[1].size_hint == "large"
    assert parsed.panels[0].visual is not None
    assert "アリスが屋上" in parsed.panels[0].visual
    assert len(parsed.panels[0].speech_intents) == 1
    assert parsed.panels[0].speech_intents[0].speaker_id == "alice"
    assert parsed.panels[0].speech_intents[0].bubble_type == "inner_monologue"
    assert parsed.panels[0].speech_intents[0].register == "静かなモノローグ"
    assert len(parsed.panels[0].sfx) == 1
    assert parsed.panels[0].sfx[0].text == "ヒュウ"
    assert parsed.panels[0].sfx[0].role == "風"
    # Panel 2: SFX is "なし"
    assert parsed.panels[1].sfx == []


def test_malformed_yaml_frontmatter_fatal() -> None:
    bad = "---\npage_number: not_an_int_but_string_ok\n[ not yaml\n---\n\n## Panel 1\n"
    result = parse_page_beat_text(bad)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.FRONTMATTER_INVALID


def test_tolerant_panel_missing_camera() -> None:
    """Phase 1 keeps Visual / Emotion present, camera=None silently."""
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1 [size: regular]

        **Visual**: 屋上
        **Emotion**: 落ち着き
        """
    )
    result = parse_page_beat_text(md)
    assert isinstance(result, Success)
    panel = result.unwrap().panels[0]
    assert panel.camera is None
    assert panel.visual == "屋上"


def test_tolerant_panel_missing_visual_kept_for_phase_2() -> None:
    """Phase 1 surfaces a panel with `visual=None`; Phase 2 will reject."""
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1

        **Emotion**: 落ち着き
        """
    )
    result = parse_page_beat_text(md)
    assert isinstance(result, Success)
    panel = result.unwrap().panels[0]
    assert panel.visual is None


def test_speech_text_strips_quote_wrappers() -> None:
    """LLMs sometimes wrap dialogue in `"..."` or `「...」`. Parser must strip
    those so the rendered text doesn't show stray quotes."""
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1

        **Visual**: x
        **Emotion**: y

        **Speech**:
        - [alice / dialogue / 静か] "本日も大気圧は安定していますか"
        - [alice / inner_monologue / 諦め] 「いつもの変な準備」
        - [alice / dialogue / 普通] 裸のセリフ
        """
    )
    result = parse_page_beat_text(md)
    assert isinstance(result, Success)
    intents = result.unwrap().panels[0].speech_intents
    assert intents[0].text == "本日も大気圧は安定していますか"
    assert intents[1].text == "いつもの変な準備"
    assert intents[2].text == "裸のセリフ"


def test_invalid_speech_line_silently_skipped() -> None:
    """Per SCHEMA: Speech format violations are warnings, NOT panel errors."""
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1

        **Visual**: x
        **Emotion**: y

        **Speech**:
        - this line is not the right format
        - [alice / dialogue / 静か] 意図
        """
    )
    result = parse_page_beat_text(md)
    assert isinstance(result, Success)
    # Only the well-formed line should appear.
    intents = result.unwrap().panels[0].speech_intents
    assert len(intents) == 1
    assert intents[0].text == "意図"


# ---------------------------------------------------------------------------
# Phase 2: strict validation
# ---------------------------------------------------------------------------


def _validate(text: str) -> Result[None, MangaError]:
    parsed = parse_page_beat_text(text)
    assert isinstance(parsed, Success)
    return validate_page_beat(
        parsed.unwrap(),
        known_character_ids=["alice", "bob"],
        known_location_ids=["rooftop_morning", "rooftop"],
        expected_page_number=None,
        max_panels_per_page=8,
    )


def test_validate_full_valid_passes() -> None:
    result = _validate(_VALID_MD)
    assert isinstance(result, Success)


def test_validate_panel_missing_visual_fails() -> None:
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1

        **Emotion**: y
        """
    )
    result = _validate(md)
    assert isinstance(result, Failure)
    assert "Visual" in result.failure().message


def test_validate_unknown_speaker_id_fails() -> None:
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1

        **Visual**: x
        **Emotion**: y

        **Speech**:
        - [stranger / dialogue / 普通] 話す
        """
    )
    result = _validate(md)
    assert isinstance(result, Failure)
    assert "unknown speaker_id" in result.failure().message


def test_validate_speaker_must_be_in_page_character_ids_not_global() -> None:
    """Round-2 review fix: speech speakers limited to THIS page's character_ids.

    Bob is a globally-known character but isn't listed in this page's
    `character_ids: [alice]`. PageRender only pulls refs for the page's
    character_ids, so a speaker outside that list ends up drawn without a
    matching ref image — must fail validation.
    """
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1

        **Visual**: x
        **Emotion**: y

        **Speech**:
        - [bob / dialogue / 普通] alice の知らないところでbob が話す
        """
    )
    # bob is in known_character_ids but NOT in this page's frontmatter.
    result = _validate(md)
    assert isinstance(result, Failure)
    assert "unknown speaker_id" in result.failure().message
    assert "bob" in result.failure().message


def test_validate_narrator_speaker_accepted() -> None:
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1

        **Visual**: x
        **Emotion**: y

        **Speech**:
        - [narrator / narration / 落ち着いた語り] 語り
        """
    )
    result = _validate(md)
    assert isinstance(result, Success)


def test_validate_invalid_size_hint_fails() -> None:
    """Round-5 review fix: invalid size_hint values must reach the user.

    Phase 1 used to coerce `[size: giant]` to "regular" before Phase 2,
    silently dropping format drift. Now Phase 1 keeps the raw value and
    Phase 2 rejects it so parse-retry can re-prompt.
    """
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1 [size: giant]

        **Visual**: x
        **Emotion**: y
        """
    )
    result = _validate(md)
    assert isinstance(result, Failure)
    msg = result.failure().message
    assert "size_hint" in msg
    assert "giant" in msg


def test_validate_invalid_bubble_type_fails() -> None:
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1

        **Visual**: x
        **Emotion**: y

        **Speech**:
        - [alice / whisper / ささやき] 話す
        """
    )
    result = _validate(md)
    assert isinstance(result, Failure)
    assert "bubble_type" in result.failure().message


def test_validate_panel_numbers_not_contiguous_fails() -> None:
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1
        **Visual**: a
        **Emotion**: b

        ## Panel 3
        **Visual**: c
        **Emotion**: d
        """
    )
    result = _validate(md)
    assert isinstance(result, Failure)
    assert "Panel ordering" in result.failure().message


def test_validate_unknown_location_id_fails() -> None:
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: void
        character_ids: [alice]
        mood: 静か
        ---

        ## Panel 1
        **Visual**: a
        **Emotion**: b
        """
    )
    result = _validate(md)
    assert isinstance(result, Failure)
    assert "unknown location_id" in result.failure().message


def test_validate_phase_not_in_arc_fails() -> None:
    """Round-1 review: frontmatter.phase must exist in PagePlan arc.phase set."""
    parsed = parse_page_beat_text(_VALID_MD)
    assert isinstance(parsed, Success)
    result = validate_page_beat(
        parsed.unwrap(),
        known_character_ids=["alice", "bob"],
        known_location_ids=["rooftop_morning"],
        expected_page_number=None,
        max_panels_per_page=8,
        known_arc_phases=["セットアップ", "結末"],  # "対立" is NOT in this set
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.VALIDATION_FAILED
    assert "phase" in result.failure().message


def test_validate_phase_must_equal_expected_outline_phase() -> None:
    """Round-3 review fix: PageBeat phase must match outline.phase exactly.

    Previously the LLM could swap to another globally-known arc phase and
    the validator would accept it, drifting the page away from the plan.
    """
    parsed = parse_page_beat_text(_VALID_MD)
    assert isinstance(parsed, Success)
    # _VALID_MD has phase="対立". Pass a different expected_phase.
    result = validate_page_beat(
        parsed.unwrap(),
        known_character_ids=["alice", "bob"],
        known_location_ids=["rooftop_morning"],
        expected_page_number=None,
        max_panels_per_page=8,
        known_arc_phases=["対立", "クライマックス"],
        expected_phase="クライマックス",  # mismatched
    )
    assert isinstance(result, Failure)
    msg = result.failure().message
    assert "does not match" in msg
    assert "クライマックス" in msg


def test_validate_location_must_equal_expected_outline_location() -> None:
    parsed = parse_page_beat_text(_VALID_MD)
    assert isinstance(parsed, Success)
    # _VALID_MD has location_id="rooftop_morning"; expect mismatch.
    result = validate_page_beat(
        parsed.unwrap(),
        known_character_ids=["alice", "bob"],
        known_location_ids=["rooftop_morning", "schoolyard"],
        expected_page_number=None,
        max_panels_per_page=8,
        expected_location_id="schoolyard",
    )
    assert isinstance(result, Failure)
    assert "location_id" in result.failure().message


def test_validate_characters_must_be_subset_of_expected() -> None:
    """character_ids may be a subset (a page may legitimately omit a
    less-prominent character from the outline), but cannot introduce
    characters absent from the outline.
    """
    parsed = parse_page_beat_text(_VALID_MD)
    assert isinstance(parsed, Success)
    # _VALID_MD has character_ids=[alice, bob]; expect only [alice].
    result = validate_page_beat(
        parsed.unwrap(),
        known_character_ids=["alice", "bob"],
        known_location_ids=["rooftop_morning"],
        expected_page_number=None,
        max_panels_per_page=8,
        expected_character_ids=["alice"],  # bob is "extra"
    )
    assert isinstance(result, Failure)
    assert "extras=['bob']" in result.failure().message


def test_validate_phase_in_arc_passes() -> None:
    parsed = parse_page_beat_text(_VALID_MD)
    assert isinstance(parsed, Success)
    result = validate_page_beat(
        parsed.unwrap(),
        known_character_ids=["alice", "bob"],
        known_location_ids=["rooftop_morning"],
        expected_page_number=None,
        max_panels_per_page=8,
        known_arc_phases=["対立", "結末"],
    )
    assert isinstance(result, Success)


def test_validate_empty_character_ids_fails() -> None:
    """Round-1 review: character_ids: [] must be rejected."""
    md = textwrap.dedent(
        """\
        ---
        page_number: 1
        phase: 起
        location_id: rooftop
        character_ids: []
        mood: 静か
        ---

        ## Panel 1
        **Visual**: x
        **Emotion**: y
        """
    )
    result = _validate(md)
    assert isinstance(result, Failure)
    assert "character_ids" in result.failure().message


def test_validate_expected_page_number_mismatch_fails() -> None:
    parsed = parse_page_beat_text(_VALID_MD)
    assert isinstance(parsed, Success)
    result = validate_page_beat(
        parsed.unwrap(),
        known_character_ids=["alice", "bob"],
        known_location_ids=["rooftop_morning"],
        expected_page_number=99,  # actual is 5
        max_panels_per_page=8,
    )
    assert isinstance(result, Failure)
    assert "expected 99" in result.failure().message
