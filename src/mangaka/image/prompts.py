"""PageRender prompt assembly.

`build_page_prompt(state, page_outline, labeled_refs, config) -> Result[str, MangaError]`
is the single entrypoint. It pulls MPBV story overview + PagePlan arc context +
the per-page outline summary + stylist sections, then asks gpt-image-2 to
compose the page (panel layout / camera / speech / narration) on its own.

> **Design note (PoC 2026-05-24)**: this used to receive a `PageBeat` with
> structured per-panel directives. PoC found the model produces stronger
> narrative pages when given the page_outline summary directly and trusted to
> handle the manga craft itself. PageBeat layer was removed; see
> `docs/ARCHITECTURE.md` 設計の進化 for details.
"""

from __future__ import annotations

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState, PageOutline
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.ref_builder import LabeledRef
from mangaka.image.sections import SECTION_SETS, extract_sections
from mangaka.logging import get_logger
from mangaka.parse.sections import extract_subsection
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)


def extract_visual_summary(description: str, *, max_chars: int) -> str:
    """Pull the visual subsection of a Character / Location description.

    Tries `### 外見` (character) then `### 視覚的特徴` (location); falls back
    to the first paragraph if neither header is present. Truncates to
    `max_chars` from the end so we don't accidentally cut mid-bullet.
    """
    body = extract_subsection(description, "外見")
    if not body:
        body = extract_subsection(description, "視覚的特徴")
    if not body:
        body = description.strip()

    body = body.strip()
    if len(body) <= max_chars:
        return body
    return body[:max_chars].rstrip() + "…"


def _character_block(
    state: MangaState, page_outline: PageOutline, config: MangakaConfig
) -> list[str]:
    """Render `【登場人物】` lines, honoring per-char and total summary budgets."""
    per_char_max = config.image.max_character_summary_chars
    total_max = config.image.max_character_summary_total_chars

    lines: list[str] = ["【登場人物】"]
    used_chars = 0
    for char_id in page_outline.character_ids:
        char = state.characters_by_id.get(char_id)
        if char is None:
            # Validated upstream; defensive only.
            continue
        summary = extract_visual_summary(char.description, max_chars=per_char_max)
        line = f"- {char.name}: {summary}"
        if used_chars + len(line) > total_max:
            break
        lines.append(line)
        used_chars += len(line)
    lines.append("")
    return lines


def build_page_prompt(
    state: MangaState,
    page_outline: PageOutline,
    labeled_refs: list[LabeledRef],
    config: MangakaConfig,
) -> Result[str, MangaError]:
    """Assemble the final Japanese prompt for a single page from PagePlan-level data.

    Returns `Failure(PROMPT_TOO_LONG)` if `len(prompt) > max_prompt_chars`.
    The orchestrator owns recovery; this function never silently truncates.
    """
    if state.stylist is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="build_page_prompt requires state.stylist",
            )
        )
    if state.mpbv is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="build_page_prompt requires state.mpbv",
            )
        )
    if state.page_plan is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="build_page_prompt requires state.page_plan",
            )
        )
    loc = state.locations_by_id.get(page_outline.location_id)
    if loc is None:
        return Failure(
            MangaError(
                kind=ErrorKind.INVALID_STATE,
                message=(
                    f"page_outline.location_id={page_outline.location_id!r} "
                    f"not in state.locations_by_id"
                ),
            )
        )

    parts: list[str] = []
    parts.append("縦長の漫画ページを 1 枚描いてください。")
    parts.append("右上から左下の読み順、日本の漫画スタイル。")
    parts.append("")

    # Story-level context: without this, gpt-image-2 composes each page in
    # isolation. MPBV §1 (logline / theme / フック) + §2 (world rules) gives
    # the model the whole-story context a manga assistant would have in mind.
    overview = extract_sections(state.mpbv.raw_markdown, [1, 2])
    if overview.strip():
        parts.append("【物語の全貌】")
        parts.append(overview)
        parts.append("")

    # Arc position for this page.
    arc_label = ""
    for a in state.page_plan.arc:
        if a.start_page <= page_outline.page_number <= a.end_page:
            arc_label = f"phase「{a.phase}」({a.summary})"
            break
    parts.append(
        f"【このページの位置】全 {state.page_plan.total_pages} ページ中、"
        f"{page_outline.page_number} ページ目。{arc_label}"
    )
    parts.append("")

    # The semantic core: PagePlan's per-page beat summary. gpt-image-2 uses
    # this to reconstruct panel layout, narration, dialogue.
    parts.append("【このページの骨格】 (PagePlan が決めた、このページに描くべきこと)")
    parts.append(page_outline.summary)
    parts.append("")

    parts.append("【場所】")
    parts.append(
        extract_visual_summary(loc.description, max_chars=config.image.max_location_summary_chars)
    )
    parts.append("")

    parts.extend(_character_block(state, page_outline, config))

    parts.append("【参照画像の構成】")
    for idx, ref in enumerate(labeled_refs, start=1):
        parts.append(f"- {idx} 枚目: {ref.label}")
    parts.append("")

    parts.append("【絵柄と演出】 (筆致・トーン・コマ運び方針)")
    parts.append("参照画像のスタイル参照画の絵柄に従ってください。")
    parts.append(extract_sections(state.stylist.raw_markdown, SECTION_SETS["page_render"]))
    parts.append("")

    parts.append("【あなたが決めること】")
    parts.append(
        "- 5-8 コマのレイアウト・サイズ・読み順 (右上から左下)。"
        "感情の山場には大コマ、繰り返し・テンポには標準コマを使い分け"
    )
    parts.append(
        "- 各コマの構図・カメラアングル・キャラのポーズと表情。上記「骨格」の感情と arc 位置を意識"
    )
    parts.append(
        "- セリフ (吹き出し): 「骨格」に引用符で書かれた key dialogue は verbatim で使う。"
        "それ以外は骨格に沿った自然な短いセリフを補う"
    )
    parts.append("- 心の声 (雲形バルーン): 主人公の重要な内省を必要に応じて")
    parts.append(
        "- ナレーション枠 (四角枠): 状況・時間経過・心情の exposition を積極的に。"
        "「骨格」を読んでいない読者にもこのページで何が起きているか伝わるようにナレ枠で補完"
    )
    parts.append("- 効果音文字 (擬音): 環境音・動作音を適度に")
    parts.append("")

    parts.append("【文字について】")
    parts.append(
        "「セリフ」「効果音」で描く文字は、漫画として自然な日本語でそのまま正確に描いてください。"
    )
    parts.append("セリフは短く口語的に (1 セリフ 30 字以内)、ナレ枠は 60 字以内。")
    parts.append("吹き出しは必ず話者の口元から伸ばす。発話者と聞き手の位置関係をコマの構図で明示。")
    parts.append(
        "1 ページ全体で 8-15 個程度の text 要素 (吹き出し + ナレ + SFX 合算) が manga として読みやすい目安。"
    )
    parts.append(
        "コマ番号・サイズ指示などのメタ情報は指示文の見出しであり、画面に文字として描かないこと。"
    )
    parts.append("")

    # Reinforcement at the tail (model attends more to the end). The earlier
    # "右上から左下" mentions get diluted across 200+ lines; output drifted to
    # LTR on the 2026-05-24 E2E PoC. Make it explicit one more time as the
    # last instruction before the model starts composing.
    parts.append("【最重要・読み順】")
    parts.append(
        "コマの読み順は必ず **右 → 左、上 → 下** (日本の漫画と同じ右綴じ)。"
        "1 コマ目は右上、最後のコマは左下になるよう配置すること。"
        "左から右に流れる西洋コミック / webtoon 風の構成にはしない。"
    )

    prompt = "\n".join(parts)

    n = len(prompt)
    if n > config.image.max_prompt_chars:
        return Failure(
            MangaError(
                kind=ErrorKind.PROMPT_TOO_LONG,
                message=(
                    f"PageRender prompt is {n} chars, exceeds "
                    f"image.max_prompt_chars={config.image.max_prompt_chars} "
                    f"(page_number={page_outline.page_number}). "
                    "Lower image.max_*_summary_chars or raise max_prompt_chars."
                ),
                detail={
                    "chars": n,
                    "limit": config.image.max_prompt_chars,
                    "page_number": page_outline.page_number,
                },
            )
        )
    if n > config.image.warn_prompt_chars:
        logger.warning(
            "page_render_prompt_large",
            chars=n,
            page_number=page_outline.page_number,
            warn_limit=config.image.warn_prompt_chars,
        )

    return Success(prompt)


__all__ = ["build_page_prompt", "extract_visual_summary"]
