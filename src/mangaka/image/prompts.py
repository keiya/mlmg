"""PageRender prompt assembly.

`build_page_prompt(state, page_beat, labeled_refs, config) -> Result[str, MangaError]`
is the single entrypoint. It pulls Stylist sections, formats panels into
Japanese natural-language directives, enforces character / location summary
budgets, and hard-fails if the prompt exceeds `image.max_prompt_chars`.
"""

from __future__ import annotations

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState, PageBeat
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.ref_builder import LabeledRef
from mangaka.image.sections import SECTION_SETS, extract_sections
from mangaka.logging import get_logger
from mangaka.parse.sections import extract_subsection
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)


_SIZE_LABELS: dict[str, str] = {
    "regular": "標準サイズ",
    "large": "キメゴマ・大ゴマ",
    "wide": "横長の広いコマ",
}
_BUBBLE_LABELS: dict[str, str] = {
    "dialogue": "通常の吹き出し",
    "inner_monologue": "心の声（雲形か角バルーン）",
    "narration": "ナレーション枠（四角枠）",
    "shout": "叫びの吹き出し（爆発型）",
}


def _size_label(s: str) -> str:
    return _SIZE_LABELS.get(s, s)


def _bubble_label(b: str) -> str:
    return _BUBBLE_LABELS.get(b, b)


def _speaker_label(state: MangaState, speaker_id: str) -> str:
    if speaker_id == "narrator":
        return "ナレーション"
    char = state.characters_by_id.get(speaker_id)
    return char.name if char is not None else speaker_id


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
        # No structured subsection — use the whole description.
        body = description.strip()

    body = body.strip()
    if len(body) <= max_chars:
        return body
    # Truncate; mark explicitly so the LLM understands content was clipped.
    return body[:max_chars].rstrip() + "…"


def _panel_lines(state: MangaState, page_beat: PageBeat) -> list[str]:
    lines: list[str] = []
    for panel in page_beat.panels:
        # No visible bullet marker — gpt-image-2 was rendering `■ コマ N` as a
        # literal "1" / "2" label inside the panel. Plain text + colon is read
        # as a directive header rather than a graphic element.
        lines.append(
            f"[Panel {panel.panel_no} / {_size_label(panel.size_hint)}]"
        )
        lines.append(f"  絵: {panel.visual}")
        if panel.camera:
            lines.append(f"  カメラ: {panel.camera}")
        lines.append(f"  感情: {panel.emotion}")
        for sp in panel.speech_intents:
            register = f"、口調: {sp.register}" if sp.register else ""
            lines.append(
                f"  セリフ: {_speaker_label(state, sp.speaker_id)} が"
                f"{_bubble_label(sp.bubble_type)}で発話"
                f"{register}。文字:「{sp.text}」"
            )
        for fx in panel.sfx:
            lines.append(f"  効果音: 「{fx.text}」（{fx.role}）")
        lines.append("")
    return lines


def _character_block(
    state: MangaState, page_beat: PageBeat, config: MangakaConfig
) -> list[str]:
    """Render `【登場人物】` lines, honoring per-char and total summary budgets."""
    per_char_max = config.image.max_character_summary_chars
    total_max = config.image.max_character_summary_total_chars

    lines: list[str] = ["【登場人物】"]
    used_chars = 0
    for char_id in page_beat.character_ids:
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
    page_beat: PageBeat,
    labeled_refs: list[LabeledRef],
    config: MangakaConfig,
) -> Result[str, MangaError]:
    """Assemble the final Japanese prompt for a single page.

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
    loc = state.locations_by_id.get(page_beat.location_id)
    if loc is None:
        return Failure(
            MangaError(
                kind=ErrorKind.INVALID_STATE,
                message=(
                    f"page_beat.location_id={page_beat.location_id!r} "
                    f"not in state.locations_by_id"
                ),
            )
        )

    parts: list[str] = []
    parts.append("縦長の漫画ページを 1 枚描いてください。")
    parts.append("右上から左下の読み順、日本の漫画スタイル。")
    parts.append("")

    # Story-level context: without this, gpt-image-2 composes each page in
    # isolation. PoC 2026-05-24 showed the model couldn't convey arc-level
    # meaning (e.g. "this is the redo of page 1") because it only saw the
    # current page's mood + continuity_note. Pulling MPBV §1 (logline /
    # theme / 異常性) and §2 (world rules) plus arc position gives the model
    # the same whole-story context a manga assistant would have in mind.
    # `extract_sections` takes first-match per section number, so we get
    # the master-plot §1/§2 (not the worldbuilding ones that share numbers).
    if state.mpbv is not None and state.page_plan is not None:
        overview = extract_sections(state.mpbv.raw_markdown, [1, 2])
        if overview.strip():
            parts.append("【物語の全貌】")
            parts.append(overview)
            parts.append("")
        arc_label = ""
        for a in state.page_plan.arc:
            if a.start_page <= page_beat.page_number <= a.end_page:
                arc_label = f"phase「{a.phase}」({a.summary})"
                break
        parts.append(
            f"【このページの位置】全 {state.page_plan.total_pages} ページ中、"
            f"{page_beat.page_number} ページ目。{arc_label}"
        )
        parts.append("")

    parts.append("【場所】")
    parts.append(
        extract_visual_summary(
            loc.description, max_chars=config.image.max_location_summary_chars
        )
    )
    parts.append("")

    parts.extend(_character_block(state, page_beat, config))

    parts.append("【このページの空気】")
    parts.append(page_beat.mood)
    if page_beat.continuity_note:
        parts.append(page_beat.continuity_note)
    parts.append("")

    parts.append(
        f"【コマ構成】{len(page_beat.panels)} コマで構成。右上から左下の読み順:"
    )
    parts.append("")
    parts.extend(_panel_lines(state, page_beat))

    parts.append("【参照画像の構成】")
    for idx, ref in enumerate(labeled_refs, start=1):
        parts.append(f"- {idx} 枚目: {ref.label}")
    parts.append("")

    parts.append("【絵柄と演出】")
    parts.append("参照画像のスタイル参照画の絵柄に従ってください。")
    parts.append(
        extract_sections(state.stylist.raw_markdown, SECTION_SETS["page_render"])
    )
    parts.append("")

    parts.append("【文字について】")
    parts.append(
        "「セリフ」「効果音」で指定された文字は、吹き出し・ナレーション枠・"
        "効果音文字として、そのまま正確な日本語で描いてください。"
    )
    parts.append(
        "吹き出しは必ず話者の口元から伸ばすこと。発話者と聞き手の位置関係を"
        "コマの構図で明示してください。"
    )
    parts.append(
        "コマ番号 (`[Panel N / ...]`) やサイズ指示は **指示文の見出しであり、"
        "画面に文字として描かないこと**。コマ内に「1」「2」のような番号ラベルを"
        "置かない。"
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
                    f"(page_number={page_beat.page_number}). "
                    "Shorten PageBeat panels, lower image.max_*_summary_chars, "
                    "or raise image.max_prompt_chars."
                ),
                detail={
                    "chars": n,
                    "limit": config.image.max_prompt_chars,
                    "page_number": page_beat.page_number,
                },
            )
        )
    if n > config.image.warn_prompt_chars:
        logger.warning(
            "page_render_prompt_large",
            chars=n,
            page_number=page_beat.page_number,
            warn_limit=config.image.warn_prompt_chars,
        )

    return Success(prompt)


__all__ = ["build_page_prompt", "extract_visual_summary"]
