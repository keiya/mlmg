"""PageBeat layer: per-page LLM call → canonical .md → parse → state update.

For each page in `state.page_plan.page_outline`:
1. Render the page-beat prompt with the PagePlan outline + Stylist narrative
   sections + previous PageBeats as context.
2. Call the LLM. Retry up to `limits.max_parse_retries` if Phase-2 validation
   fails, feeding the validator error back into the prompt.
3. Write the Markdown to `page_beats/page_beat_NNN.md` (versioned save —
   the pipeline never overwrites canonical artifacts).
4. Convert the `ParsedPageBeat` into the frozen `PageBeat` domain object and
   append it to `state.pages` (with `image_path=None`; PageRender fills it in).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState, Page, PageBeat, Panel
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.assets import save_bytes
from mangaka.image.client import ImageClient
from mangaka.image.sections import SECTION_SETS, extract_sections
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.parse.page_beat import (
    ParsedPageBeat,
    parse_page_beat_text,
    validate_page_beat,
)
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

TEMPLATE_NAME = "08_page_beat.md"
PREV_CONTEXT_PAGES = 2  # how many earlier PageBeats to include as context


def _format_id_block(items: list[tuple[str, str]]) -> str:
    if not items:
        return "(なし)"
    return "\n".join(f"- `{i}` — {n}" for i, n in items)


def _previous_pagebeat_context(state: MangaState, page_number: int) -> tuple[str, int]:
    """Concatenate the last `PREV_CONTEXT_PAGES` PageBeat markdowns (if any)."""
    chunks: list[str] = []
    count = 0
    for n in range(page_number - PREV_CONTEXT_PAGES, page_number):
        page = state.pages_by_number.get(n)
        if page is None:
            continue
        try:
            md = page.beat.md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "previous_page_beat_read_failed",
                page_number=n,
                error=str(exc),
            )
            continue
        chunks.append(f"### ページ {n}\n\n{md}")
        count += 1
    return ("\n\n".join(chunks), count)


def _to_domain_page_beat(
    parsed: ParsedPageBeat, md_path: Path
) -> PageBeat:
    """Convert the parsed structure into the frozen domain dataclass.

    Phase 2 validation has already confirmed all required fields are present,
    so the asserts are invariant checks (programmer error if they fire).
    """
    fm = parsed.frontmatter
    assert fm.page_number is not None
    assert fm.phase is not None
    assert fm.location_id is not None
    assert fm.character_ids is not None
    assert fm.mood is not None

    panels: list[Panel] = []
    for p in parsed.panels:
        assert p.visual is not None
        assert p.emotion is not None
        panels.append(
            Panel(
                panel_no=p.panel_no,
                size_hint=p.size_hint,
                visual=p.visual,
                emotion=p.emotion,
                camera=p.camera,
                speech_intents=list(p.speech_intents),
                sfx=list(p.sfx),
            )
        )

    # Auto-augment `character_ids` with any Speech speakers the LLM put in
    # panels but forgot to list in frontmatter. The validator (now relaxed)
    # accepts any global character as a speaker; here we make sure the
    # PageBeat's `character_ids` covers all of them so `build_refs` picks
    # up the right character sheets. Reserved IDs (narrator etc.) are
    # excluded — they're not real characters.
    from mangaka.parse.page_beat import SPEAKER_RESERVED_IDS

    augmented = list(fm.character_ids)
    seen = set(augmented)
    for p in parsed.panels:
        for sp in p.speech_intents:
            if sp.speaker_id not in seen and sp.speaker_id not in SPEAKER_RESERVED_IDS:
                augmented.append(sp.speaker_id)
                seen.add(sp.speaker_id)

    return PageBeat(
        page_number=fm.page_number,
        phase=fm.phase,
        location_id=fm.location_id,
        character_ids=augmented,
        mood=fm.mood,
        continuity_note=fm.continuity_note,
        panels=panels,
        md_path=md_path,
    )


def _render_one_page(
    state: MangaState,
    *,
    page_number: int,
    llm: LLMClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
    run_dir: Path,
) -> Result[Page, MangaError]:
    """Generate, parse, validate, and persist a single PageBeat."""
    assert state.page_plan is not None
    assert state.stylist is not None
    outline = state.page_plan.page_outline[page_number - 1]
    assert outline.page_number == page_number  # contract enforced by PagePlan parser

    stylist_sections = extract_sections(
        state.stylist.raw_markdown, SECTION_SETS["page_beat"]
    )

    prev_context, prev_count = _previous_pagebeat_context(state, page_number)

    # YAML inline list with EACH id quoted (e.g. `["alice", "bob"]`).
    # Plain `[alice, bob]` is fine for most ids, but YAML 1.1 parses bare
    # scalars like `on`, `off`, `yes`, `no`, `null` as booleans/null — and
    # those happen to match our `[a-z][a-z0-9_]{1,31}` id regex. Quoting
    # is the cheapest way to dodge the entire scalar-coercion footgun.
    character_ids_yaml = "[" + ", ".join(f'"{cid}"' for cid in outline.character_ids) + "]"

    base_prompt_result = prompt_loader.render(
        TEMPLATE_NAME,
        mpbv=(state.mpbv.raw_markdown if state.mpbv is not None else ""),
        stylist_sections=stylist_sections,
        page_number=page_number,
        total_pages=state.page_plan.total_pages,
        phase=outline.phase,
        page_summary=outline.summary,
        character_ids_block=_format_id_block(
            [
                (cid, state.characters_by_id[cid].name)
                for cid in outline.character_ids
                if cid in state.characters_by_id
            ]
        ),
        character_ids_yaml=character_ids_yaml,
        primary_character_id=outline.character_ids[0],
        location_id=outline.location_id,
        known_character_ids_block=_format_id_block(
            [(c.id, c.name) for c in state.characters]
        ),
        known_location_ids_block=_format_id_block(
            [(loc.id, loc.name) for loc in state.locations]
        ),
        previous_page_beats=prev_context,
        previous_page_count=prev_count,
        max_panels_per_page=config.limits.max_panels_per_page,
    )
    if isinstance(base_prompt_result, Failure):
        return Failure(base_prompt_result.failure())
    base_prompt = base_prompt_result.unwrap()

    layer = config.layers.page_beat
    max_attempts = config.limits.max_parse_retries + 1
    last_error: MangaError | None = None
    current_prompt = base_prompt

    for attempt in range(max_attempts):
        response_result = llm.complete(
            current_prompt,
            model=layer.model,
            temperature=layer.temperature,
            max_tokens=layer.max_tokens,
            thinking=layer.thinking,
            reasoning_effort=layer.reasoning_effort,
        )
        if isinstance(response_result, Failure):
            return Failure(response_result.failure())
        raw_md = response_result.unwrap()

        parsed = parse_page_beat_text(raw_md)
        if isinstance(parsed, Failure):
            last_error = parsed.failure()
        else:
            assert state.page_plan is not None  # guarded by entry check
            validated = validate_page_beat(
                parsed.unwrap(),
                known_character_ids=[c.id for c in state.characters],
                known_location_ids=[loc.id for loc in state.locations],
                expected_page_number=page_number,
                max_panels_per_page=config.limits.max_panels_per_page,
                known_arc_phases=[p.phase for p in state.page_plan.arc],
                expected_phase=outline.phase,
                expected_location_id=outline.location_id,
                expected_character_ids=outline.character_ids,
            )
            if isinstance(validated, Success):
                save_result = save_bytes(
                    run_dir / "page_beats" / f"page_beat_{page_number:03d}.md",
                    raw_md.encode("utf-8"),
                )
                if isinstance(save_result, Failure):
                    return Failure(save_result.failure())
                md_path = save_result.unwrap()
                page_beat = _to_domain_page_beat(parsed.unwrap(), md_path)
                logger.info(
                    "page_beat_completed",
                    page_number=page_number,
                    panels=len(page_beat.panels),
                    parse_attempts=attempt + 1,
                )
                return Success(Page(page_number=page_number, beat=page_beat, image_path=None))
            last_error = validated.failure()

        logger.warning(
            "page_beat_parse_failed",
            page_number=page_number,
            attempt=attempt + 1,
            max_attempts=max_attempts,
            error=last_error.message,
        )
        current_prompt = (
            f"{base_prompt}\n\n"
            "# 直前の出力の検証結果\n"
            "前回の出力は以下の理由で却下されました。**前回の出力と全く同じものは"
            "出力せず**、下記指摘を必ず反映した上で再生成してください:\n\n"
            f"- {last_error.message}"
        )

    assert last_error is not None
    logger.error(
        "page_beat_exhausted",
        page_number=page_number,
        attempts=max_attempts,
        error=last_error.message,
    )
    return Failure(last_error)


def generate_page_beat_layer(
    state: MangaState,
    llm: LLMClient,
    img: ImageClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
    *,
    run_dir: Path,
) -> Result[MangaState, MangaError]:
    """Generate PageBeat for every page in PagePlan, appending to state.pages.

    Signature matches `ImageLayerFn` (with `img` and `run_dir`) so the pipeline
    orchestrator can dispatch it uniformly with PageRender — even though this
    layer doesn't actually call `img`. PageBeat needs `run_dir` to persist the
    per-page canonical `.md` files; tying `run_dir` to image-bearing layers
    keeps the orchestrator's two-category split clean.
    """
    _ = img  # not used by this layer; see docstring
    logger.info("layer_started", layer="page_beat")

    if state.page_plan is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="page_beat layer requires page_plan",
            )
        )
    if state.stylist is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="page_beat layer requires stylist",
            )
        )

    current = state
    for outline in state.page_plan.page_outline:
        page_result = _render_one_page(
            current,
            page_number=outline.page_number,
            llm=llm,
            config=config,
            prompt_loader=prompt_loader,
            run_dir=run_dir,
        )
        if isinstance(page_result, Failure):
            return Failure(page_result.failure())
        current = replace(current, pages=[*current.pages, page_result.unwrap()])

    logger.info("layer_completed", layer="page_beat", pages=len(current.pages))
    return Success(current)


__all__ = ["generate_page_beat_layer"]
