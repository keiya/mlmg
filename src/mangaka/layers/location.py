"""Location layer: MPBV + Stylist → list[Location] + per-loc sheet PNGs.

Mirrors character.py: parallelized via run_image_jobs (plan §3.4) with
LLM-output caching for resume (plan §3.8).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from mangaka.config import MangakaConfig
from mangaka.domain import Location, MangaState, Stylist
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.client import ImageClient
from mangaka.image.parallel import ImageJob, ImageJobOutcome, run_image_jobs
from mangaka.image.sections import SECTION_SETS, extract_sections
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.parse.location import ParsedLocation, parse_location_markdown
from mangaka.parse.sections import extract_subsection
from mangaka.persistence import save_state, state_path_for
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

TEXT_TEMPLATE = "06_location.md"
IMAGE_TEMPLATE = "06b_location_sheet.md"


def _build_sheet_prompt(
    parsed: ParsedLocation,
    stylist_md: str,
    prompt_loader: PromptLoader,
) -> Result[str, MangaError]:
    visual_block = extract_subsection(parsed.description, "視覚的特徴")
    if not visual_block.strip():
        return Failure(
            MangaError(
                kind=ErrorKind.PARSE_ERROR,
                message=f"location {parsed.id!r} missing 視覚的特徴 section",
                detail={"location_id": parsed.id},
            )
        )
    stylist_sections = extract_sections(stylist_md, SECTION_SETS["location_sheet"])
    return prompt_loader.render(
        IMAGE_TEMPLATE,
        visual_block=visual_block,
        stylist_sections=stylist_sections,
    )


def _build_jobs(
    todo: list[ParsedLocation],
    stylist: Stylist,
    prompt_loader: PromptLoader,
    config: MangakaConfig,
    run_dir: Path,
) -> Result[list[ImageJob], MangaError]:
    jobs: list[ImageJob] = []
    for parsed in todo:
        prompt_result = _build_sheet_prompt(parsed, stylist.raw_markdown, prompt_loader)
        if isinstance(prompt_result, Failure):
            return Failure(prompt_result.failure())
        jobs.append(
            ImageJob(
                id=f"location_{parsed.id}",
                prompt=prompt_result.unwrap(),
                refs=[stylist.style_ref_path],
                output_path=run_dir / "assets" / "locations" / f"{parsed.id}.png",
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
    # See character.py: truthiness over `is not None` so empty-string
    # cache (corrupted) falls back to LLM rather than emitting a
    # confusing PARSE_ERROR.
    if state.location_markdown:
        logger.info("location_layer_resume", source="cached_markdown")
        return Success((state, state.location_markdown))

    layer = config.layers.location
    assert state.mpbv is not None
    text_prompt_result = prompt_loader.render(
        TEXT_TEMPLATE,
        mpbv=state.mpbv.raw_markdown,
        stylist=stylist.raw_markdown,
        max_locations=config.limits.max_locations,
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
            "layer_failed", layer="location", phase="text",
            error=text_result.failure().message,
        )
        return Failure(text_result.failure())

    raw_markdown = text_result.unwrap()
    state = replace(state, location_markdown=raw_markdown)
    cache_save = save_state(state, state_path_for(run_dir, "location"))
    if isinstance(cache_save, Failure):
        return Failure(cache_save.failure())
    return Success((state, raw_markdown))


def generate_location_layer(
    state: MangaState,
    llm: LLMClient,
    img: ImageClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
    *,
    run_dir: Path,
) -> Result[MangaState, MangaError]:
    logger.info("layer_started", layer="location")

    if state.mpbv is None or state.stylist is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="location layer requires mpbv and stylist",
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

    parsed_result = parse_location_markdown(raw_markdown)
    if isinstance(parsed_result, Failure):
        return Failure(parsed_result.failure())
    parsed_locs = parsed_result.unwrap()

    if len(parsed_locs) > config.limits.max_locations:
        logger.warning(
            "location_count_exceeded",
            count=len(parsed_locs),
            limit=config.limits.max_locations,
        )

    for parsed in parsed_locs:
        if not extract_subsection(parsed.description, "視覚的特徴").strip():
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"location {parsed.id!r} missing 視覚的特徴 section",
                    detail={"location_id": parsed.id},
                )
            )

    already_done = {loc.id for loc in current.locations}
    todo = [p for p in parsed_locs if p.id not in already_done]
    if not todo:
        logger.info(
            "layer_completed", layer="location",
            count=len(current.locations), resumed=True,
        )
        return Success(current)

    jobs_result = _build_jobs(todo, stylist, prompt_loader, config, run_dir)
    if isinstance(jobs_result, Failure):
        return Failure(jobs_result.failure())

    checkpoint_path = state_path_for(run_dir, "location")
    parsed_order = {p.id: i for i, p in enumerate(parsed_locs)}

    def on_complete(outcome: ImageJobOutcome) -> Result[None, MangaError]:
        nonlocal current
        if isinstance(outcome.result, Failure):
            return Failure(outcome.result.failure())
        parsed = cast("ParsedLocation", outcome.job.tag)
        new_loc = Location(
            id=parsed.id,
            name=parsed.name,
            description=parsed.description,
            sheet_path=outcome.result.unwrap(),
        )
        merged = [*current.locations, new_loc]
        merged.sort(key=lambda loc: parsed_order.get(loc.id, len(parsed_order)))
        current = replace(current, locations=merged)
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

    logger.info("layer_completed", layer="location", count=len(current.locations))
    return Success(current)


__all__ = ["generate_location_layer"]
