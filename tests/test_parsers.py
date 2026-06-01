"""Tests for character / location Markdown parsers."""

from __future__ import annotations

import textwrap

from returns.result import Failure, Success

from mangaka.errors import ErrorKind
from mangaka.parse.character import parse_character_markdown
from mangaka.parse.location import parse_location_markdown

# ---------------------------------------------------------------------------
# Character
# ---------------------------------------------------------------------------


def test_character_single() -> None:
    md = textwrap.dedent(
        """\
        ## アリス (alice)

        ### 基本情報
        - 年齢: 17

        ### 外見 (Visual Identity)
        - 髪型: ボブ
        """
    )
    result = parse_character_markdown(md)
    assert isinstance(result, Success)
    chars = result.unwrap()
    assert len(chars) == 1
    assert chars[0].id == "alice"
    assert chars[0].name == "アリス"
    assert "ボブ" in chars[0].description


def test_character_multiple_split_correctly() -> None:
    md = textwrap.dedent(
        """\
        ## アリス (alice)
        ### 外見
        ボブカット

        ## ボブ (bob)
        ### 外見
        坊主
        """
    )
    result = parse_character_markdown(md)
    assert isinstance(result, Success)
    chars = result.unwrap()
    assert [c.id for c in chars] == ["alice", "bob"]
    assert "ボブカット" in chars[0].description
    assert "ボブカット" not in chars[1].description
    assert "坊主" in chars[1].description


def test_character_reserved_id_rejected() -> None:
    md = "## ナレーター (narrator)\n### 外見\nなし"
    result = parse_character_markdown(md)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR


def test_character_duplicate_id_rejected() -> None:
    md = "## A (alice)\n### 外見\nx\n\n## B (alice)\n### 外見\ny"
    result = parse_character_markdown(md)
    assert isinstance(result, Failure)


def test_character_no_blocks_is_failure() -> None:
    result = parse_character_markdown("just a paragraph, no headers")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR


def test_character_invalid_id_format_rejected() -> None:
    """ID must start with [a-z] and use [a-z0-9_]. Mixed-case or hyphenated fails.

    Regression guard for M2 round-1 review: previously the header regex
    pre-filter rejected invalid IDs at match time, so a malformed entity
    after a valid one was silently appended to the previous block's
    description instead of producing a typed PARSE_ERROR.
    """
    md = "## A (Alice)\n### 外見\nx"
    result = parse_character_markdown(md)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR


def test_character_invalid_id_after_valid_raises_not_dropped() -> None:
    """A malformed second header must error out, not get merged into block 1."""
    md = textwrap.dedent(
        """\
        ## アリス (alice)
        ### 外見
        ボブカット

        ## ボブ (Bob)
        ### 外見
        坊主
        """
    )
    result = parse_character_markdown(md)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR
    # The intent: the user should see an error, not a silently truncated result.
    assert "Bob" in result.failure().message


def test_character_bare_h2_without_parens_rejected() -> None:
    """Regression guard for M2 round-2 P2 fix.

    `## ボブ` without `(id)` previously got silently absorbed into the
    previous character's description. It must now produce PARSE_ERROR.
    """
    md = textwrap.dedent(
        """\
        ## アリス (alice)
        ### 外見
        ボブカット

        ## ボブ
        ### 外見
        坊主
        """
    )
    result = parse_character_markdown(md)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR
    assert "missing `(id)`" in result.failure().message


def test_character_header_with_trailing_notes_accepted() -> None:
    """`## アリス (alice) — 主人公` should parse cleanly with trailing notes."""
    md = textwrap.dedent(
        """\
        ## アリス (alice) — 主人公
        ### 外見
        ボブカット
        """
    )
    result = parse_character_markdown(md)
    assert isinstance(result, Success)
    chars = result.unwrap()
    assert chars[0].id == "alice"
    assert chars[0].name == "アリス"


# ---------------------------------------------------------------------------
# Location
# ---------------------------------------------------------------------------


def test_location_with_time_variant_ids() -> None:
    md = textwrap.dedent(
        """\
        ## 屋上 朝 (rooftop_morning)
        ### 視覚的特徴
        朝日が差し込む

        ## 屋上 夜 (rooftop_night)
        ### 視覚的特徴
        月光下
        """
    )
    result = parse_location_markdown(md)
    assert isinstance(result, Success)
    locs = result.unwrap()
    assert [loc.id for loc in locs] == ["rooftop_morning", "rooftop_night"]


def test_location_duplicate_id_rejected() -> None:
    md = "## a (rooftop)\n### 視覚的特徴\nx\n\n## b (rooftop)\n### 視覚的特徴\ny"
    result = parse_location_markdown(md)
    assert isinstance(result, Failure)


def test_location_reserved_id_rejected() -> None:
    """Round-1 symmetry fix: location parser also rejects `none` / `null`."""
    md = "## どこか (none)\n### 視覚的特徴\nどこにもない場所"
    result = parse_location_markdown(md)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR


def test_location_reserved_id_self_rejected() -> None:
    """Round-3 fix: SCHEMA.md §2 reserves `self` for all usages, not just chars."""
    md = "## ここ (self)\n### 視覚的特徴\nここ"
    result = parse_location_markdown(md)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR


def test_location_bare_h2_without_parens_rejected() -> None:
    md = textwrap.dedent(
        """\
        ## 屋上 (rooftop)
        ### 視覚的特徴
        広い

        ## 教室
        ### 視覚的特徴
        埃っぽい
        """
    )
    result = parse_location_markdown(md)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR


def test_location_invalid_id_after_valid_raises_not_dropped() -> None:
    md = textwrap.dedent(
        """\
        ## 屋上 (rooftop)
        ### 視覚的特徴
        広い

        ## 教室 (Class-3A)
        ### 視覚的特徴
        埃っぽい
        """
    )
    result = parse_location_markdown(md)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR


def test_character_fullwidth_parens() -> None:
    md = textwrap.dedent(
        """\
        ## アリス（alice）
        ### 外見
        ボブカット
        """
    )
    result = parse_character_markdown(md)
    assert isinstance(result, Success)
    chars = result.unwrap()
    assert len(chars) == 1
    assert chars[0].id == "alice"
    assert chars[0].name == "アリス"


def test_location_fullwidth_parens() -> None:
    md = textwrap.dedent(
        """\
        ## 屋上（rooftop_morning）
        ### 視覚的特徴
        広い
        """
    )
    result = parse_location_markdown(md)
    assert isinstance(result, Success)
    locs = result.unwrap()
    assert len(locs) == 1
    assert locs[0].id == "rooftop_morning"
    assert locs[0].name == "屋上"


def test_character_multiple_paren_groups_picks_last() -> None:
    md = textwrap.dedent(
        """\
        ## アリス（17歳・高校生）（alice）
        ### 外見
        ボブ
        """
    )
    result = parse_character_markdown(md)
    assert isinstance(result, Success)
    chars = result.unwrap()
    assert len(chars) == 1
    assert chars[0].id == "alice"
    assert "17歳" in chars[0].name


def test_location_multiple_paren_groups_picks_last() -> None:
    md = textwrap.dedent(
        """\
        ## マルハチ青潮南店（店内）（maruhachi_interior_night）
        ### 視覚的特徴
        蛍光灯
        """
    )
    result = parse_location_markdown(md)
    assert isinstance(result, Success)
    locs = result.unwrap()
    assert len(locs) == 1
    assert locs[0].id == "maruhachi_interior_night"
    assert "店内" in locs[0].name


def test_character_backtick_wrapped_id_accepted() -> None:
    """LLMs often emit the id as markdown inline code: ``## アリス (`alice`)``.

    The backticks are cosmetic; the parser strips them so an otherwise-valid
    id doesn't fail the run.
    """
    md = "## アリス (`alice`)\n### 外見\nボブカット"
    result = parse_character_markdown(md)
    assert isinstance(result, Success)
    chars = result.unwrap()
    assert chars[0].id == "alice"
    assert chars[0].name == "アリス"


def test_location_backtick_wrapped_id_accepted() -> None:
    """Regression guard for the konbini_comedy run.

    The LLM wrapped the id in markdown inline code,
    ``(`convenience_store`)``, which failed `_VALID_ID_RE` and crashed the
    run at the location layer. Uses the real multi-paren header shape (a
    descriptive fullwidth group before the id group).
    """
    md = (
        "## ヨリミチマート南町店（外観＋売り場一体設定） (`convenience_store`)\n"
        "### 視覚的特徴\n明るい蛍光灯"
    )
    result = parse_location_markdown(md)
    assert isinstance(result, Success)
    locs = result.unwrap()
    assert locs[0].id == "convenience_store"


def test_backtick_strip_is_tolerant_but_revalidated() -> None:
    """Tolerant by design: stray / unbalanced / multiple backticks all resolve
    to the clean id, because the result is still checked by `_VALID_ID_RE`.

    Strictly rejecting unbalanced backticks would reintroduce the exact
    fragility this strip removes (a run crashing on cosmetic LLM drift), with
    no safety gain — the stripped id is re-validated below either way.
    """
    for header in ("## アリス (`alice)", "## アリス (alice`)", "## アリス (```alice```)"):
        result = parse_character_markdown(f"{header}\n### 外見\nx")
        assert isinstance(result, Success), header
        assert result.unwrap()[0].id == "alice"


def test_interior_backtick_still_rejected() -> None:
    """Strip only touches the edges: an interior backtick is not a valid id
    char, so `` (`a`b`) `` -> `a`b` still fails validation (no silent accept)."""
    result = parse_location_markdown("## X (`a`b`)\n### 視覚的特徴\nx")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR


def test_empty_codespan_id_rejected() -> None:
    """An empty code span `` (``) `` strips to '' and must fail, not pass."""
    result = parse_location_markdown("## X (``)\n### 視覚的特徴\nx")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.PARSE_ERROR
