"""Character layer: MPBV + Stylist → list[Character] + per-char sheet PNGs.

Parallelized via `run_image_jobs` (plan §3.4): each parsed character
becomes one ImageJob; the on_complete callback appends the result to
state.characters in canonical (parsed_chars) order and checkpoints.

Resume idempotency (plan §3.8):
- The raw LLM markdown is cached in `state.character_markdown` and
  persisted to `state_05_character.json` BEFORE any image call. On
  re-entry, if the cached markdown is present we skip the LLM call
  and re-parse it — same `parsed_chars`, same ids, prefix-skip works.
- `state.characters` may already contain a strict prefix (or sparse
  subset) from a previous partial run. We filter the job batch to
  only render characters whose `id` isn't already in state.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from mangaka.config import MangakaConfig
from mangaka.domain import Character, MangaState, Stylist
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.client import ImageClient
from mangaka.image.parallel import ImageJob, ImageJobOutcome, run_image_jobs
from mangaka.image.sections import SECTION_SETS, extract_sections
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.parse.character import ParsedCharacter, parse_character_markdown
from mangaka.parse.sections import extract_subsection
from mangaka.persistence import save_state, state_path_for
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

TEXT_TEMPLATE = "05_character.md"
IMAGE_TEMPLATE = "05b_character_sheet.md"


def _build_sheet_prompt(
    parsed: ParsedCharacter,
    stylist_md: str,
    prompt_loader: PromptLoader,
) -> Result[str, MangaError]:
    """Per-character image prompt (extracted so we can pre-flight all
    prompts before any image call)."""
    visual_block = extract_subsection(parsed.description, "外見")
    if not visual_block.strip():
        return Failure(
            MangaError(
                kind=ErrorKind.PARSE_ERROR,
                message=f"character {parsed.id!r} missing 外見 section",
                detail={"character_id": parsed.id},
            )
        )
    stylist_sections = extract_sections(stylist_md, SECTION_SETS["character_sheet"])
    return prompt_loader.render(
        IMAGE_TEMPLATE,
        visual_block=visual_block,
        stylist_sections=stylist_sections,
    )


def _build_jobs(
    todo: list[ParsedCharacter],
    stylist: Stylist,
    prompt_loader: PromptLoader,
    config: MangakaConfig,
    run_dir: Path,
) -> Result[list[ImageJob], MangaError]:
    """Build one ImageJob per character that still needs rendering. Any
    prompt-assembly failure aborts the batch before any image call."""
    jobs: list[ImageJob] = []
    for parsed in todo:
        prompt_result = _build_sheet_prompt(parsed, stylist.raw_markdown, prompt_loader)
        if isinstance(prompt_result, Failure):
            return Failure(prompt_result.failure())
        jobs.append(
            ImageJob(
                id=f"character_{parsed.id}",
                prompt=prompt_result.unwrap(),
                refs=[stylist.style_ref_path],
                output_path=run_dir / "assets" / "characters" / f"{parsed.id}.png",
                size=config.image_provider.default_size,
                quality=config.image_provider.quality,
                model=config.image_provider.model,
                tag=parsed,
            )
        )
    return Success(jobs)


def _run_text_phase(
    state: MangaState,
    stylist: Stylist,
    llm: LLMClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
    run_dir: Path,
) -> Result[tuple[MangaState, str], MangaError]:
    """Run the LLM phase if not already cached. Returns (state, raw_markdown).

    plan §3.8 invariant: on first entry, call LLM + persist markdown
    before any image call. On resume, reuse cached markdown (LLM is
    stochastic; re-running would drift the parsed ids).
    """
    # Truthiness (not `is not None`) so an empty-string cache is treated
    # as "missing" rather than "skip LLM + parse empty → confusing
    # PARSE_ERROR". Empty raw_markdown is never a legitimate cached value.
    if state.character_markdown:
        logger.info("character_layer_resume", source="cached_markdown")
        return Success((state, state.character_markdown))

    layer = config.layers.character
    assert state.mpbv is not None  # checked by caller
    text_prompt_result = prompt_loader.render(
        TEXT_TEMPLATE,
        mpbv=state.mpbv.raw_markdown,
        stylist=stylist.raw_markdown,
        max_main_characters=config.limits.max_main_characters,
    )
    if isinstance(text_prompt_result, Failure):
        return Failure(text_prompt_result.failure())

    text_result = llm.complete(
        text_prompt_result.unwrap(),
        model=layer.model,
        temperature=layer.temperature,
        max_tokens=layer.max_tokens,
        thinking=layer.thinking,
        reasoning_effort=layer.reasoning_effort,
    )
    if isinstance(text_result, Failure):
        logger.error(
            "layer_failed", layer="character", phase="text",
            error=text_result.failure().message,
        )
        return Failure(text_result.failure())

    raw_markdown = text_result.unwrap()
    state = replace(state, character_markdown=raw_markdown)
    cache_save = save_state(state, state_path_for(run_dir, "character"))
    if isinstance(cache_save, Failure):
        return Failure(cache_save.failure())
    return Success((state, raw_markdown))


def generate_character_layer(
    state: MangaState,
    llm: LLMClient,
    img: ImageClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
    *,
    run_dir: Path,
) -> Result[MangaState, MangaError]:
    """Generate the cast: LLM description Markdown + sheet PNG per character."""
    logger.info("layer_started", layer="character")

    if state.mpbv is None or state.stylist is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="character layer requires mpbv and stylist",
                detail={
                    "have_mpbv": state.mpbv is not None,
                    "have_stylist": state.stylist is not None,
                },
            )
        )

    stylist = state.stylist

    text_phase = _run_text_phase(state, stylist, llm, config, prompt_loader, run_dir)
    if isinstance(text_phase, Failure):
        return Failure(text_phase.failure())
    current, raw_markdown = text_phase.unwrap()

    parsed_result = parse_character_markdown(raw_markdown)
    if isinstance(parsed_result, Failure):
        return Failure(parsed_result.failure())
    parsed_chars = parsed_result.unwrap()

    # Warn-only on over-count (PoC 2026-05-24: see commit 05dca27).
    if len(parsed_chars) > config.limits.max_main_characters:
        logger.warning(
            "character_count_exceeded",
            count=len(parsed_chars),
            limit=config.limits.max_main_characters,
        )

    # Pre-flight: every parsed character must expose `### 外見` BEFORE any
    # image call. Otherwise a malformed late block burns paid calls on
    # earlier ones. _build_jobs catches this per-character; do an explicit
    # pre-pass so the rejection happens before we touch the executor.
    for parsed in parsed_chars:
        if not extract_subsection(parsed.description, "外見").strip():
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"character {parsed.id!r} missing 外見 section",
                    detail={"character_id": parsed.id},
                )
            )

    # Resume filter: skip characters whose sheet is already in state.
    already_done = {c.id for c in current.characters}
    todo = [p for p in parsed_chars if p.id not in already_done]
    if not todo:
        logger.info(
            "layer_completed", layer="character",
            count=len(current.characters), resumed=True,
        )
        return Success(current)

    jobs_result = _build_jobs(todo, stylist, prompt_loader, config, run_dir)
    if isinstance(jobs_result, Failure):
        return Failure(jobs_result.failure())

    checkpoint_path = state_path_for(run_dir, "character")
    # Index the parsed list so on_complete can place new Characters in
    # canonical (parsed_chars) order rather than completion order.
    parsed_order = {p.id: i for i, p in enumerate(parsed_chars)}

    def on_complete(outcome: ImageJobOutcome) -> Result[None, MangaError]:
        nonlocal current
        if isinstance(outcome.result, Failure):
            return Failure(outcome.result.failure())
        parsed = cast("ParsedCharacter", outcome.job.tag)
        new_char = Character(
            id=parsed.id,
            name=parsed.name,
            description=parsed.description,
            sheet_paths=[outcome.result.unwrap()],
        )
        merged = [*current.characters, new_char]
        merged.sort(key=lambda c: parsed_order.get(c.id, len(parsed_order)))
        current = replace(current, characters=merged)
        return save_state(current, checkpoint_path)

    exec_result = run_image_jobs(
        jobs_result.unwrap(),
        img,
        max_workers=config.concurrency.image_workers,
        on_complete=on_complete,
        fail_fast=True,
    )
    if isinstance(exec_result, Failure):
        return Failure(exec_result.failure())

    logger.info("layer_completed", layer="character", count=len(current.characters))
    return Success(current)


__all__ = ["generate_character_layer"]
