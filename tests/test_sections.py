"""Tests for stylist section extraction and generic subsection lookup."""

from __future__ import annotations

import textwrap

from mangaka.image.sections import SECTION_SETS, extract_sections
from mangaka.parse.sections import extract_subsection

_STYLIST_MD = textwrap.dedent(
    """\
    # Style Guide

    ## 1. ジャンル・トーン
    シリアスめ少年漫画。

    ## 2. テンポと演出密度
    1ページ5コマ前後。

    ## 3. セリフとモノローグの傾向
    モノローグ多め。

    ## 4. 全体の絵柄方針
    線画ベース、コントラスト強。

    ## 5. 線
    ペン入りデジタル線、強弱あり。

    ## 6. 配色とトーン
    寒色寄り、青と紫主体。

    ## 7. キャラデザインの傾向
    瞳大きめ、リアル寄り頭身。

    ## 8. 背景と空間
    密度高、書き込み多め。

    ## 9. コマと演出
    フキダシ多め、効果線細め。

    ## 10. 禁止事項
    写実シェーディング禁止、変な指禁止。
    """
)


def test_extract_sections_style_ref_set() -> None:
    out = extract_sections(_STYLIST_MD, SECTION_SETS["style_ref"])
    assert "## 4. 全体の絵柄方針" in out
    assert "## 5. 線" in out
    assert "## 6. 配色とトーン" in out
    assert "## 10. 禁止事項" in out
    # Should NOT include narrative or other visual sections.
    assert "## 1. ジャンル・トーン" not in out
    assert "## 7. キャラデザインの傾向" not in out


def test_extract_sections_character_sheet_set_includes_7() -> None:
    out = extract_sections(_STYLIST_MD, SECTION_SETS["character_sheet"])
    assert "## 7. キャラデザインの傾向" in out
    assert "## 8. 背景と空間" not in out  # 8 is for location_sheet, not char


def test_extract_sections_preserves_order() -> None:
    out = extract_sections(_STYLIST_MD, [10, 4])
    # Order in `section_nos` should be respected.
    assert out.index("## 10. 禁止事項") < out.index("## 4. 全体の絵柄方針")


def test_extract_sections_missing_section_skipped() -> None:
    short_md = "## 4. 絵柄\n本文"
    out = extract_sections(short_md, [4, 99])
    assert "## 4." in out
    # Section 99 silently absent.


def test_extract_subsection_finds_h3() -> None:
    md = textwrap.dedent(
        """\
        ## アリス (alice)

        ### 基本情報
        - 年齢: 17

        ### 外見 (Visual Identity)
        - 髪型: ボブ
        - 色: 黒

        ### 性格と口調
        快活
        """
    )
    body = extract_subsection(md, "外見")
    assert "髪型: ボブ" in body
    assert "色: 黒" in body
    # Stops before next ###.
    assert "性格と口調" not in body
    assert "快活" not in body


def test_extract_subsection_missing_returns_empty() -> None:
    assert extract_subsection("## x\n### Other\nbody", "外見") == ""
