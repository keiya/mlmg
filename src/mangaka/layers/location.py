"""Location layer: MPBV + Stylist → list[Location] + per-loc sheet PNGs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mangaka.config import MangakaConfig
from mangaka.domain import Location, MangaState
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.assets import save_bytes
from mangaka.image.client import ImageClient
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


def _render_sheet(
    parsed: ParsedLocation,
    style_ref_path: Path,
    stylist_md: str,
    prompt_loader: PromptLoader,
    img: ImageClient,
    config: MangakaConfig,
    run_dir: Path,
) -> Result[Location, MangaError]:
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
    prompt_result = prompt_loader.render(
        IMAGE_TEMPLATE,
        visual_block=visual_block,
        stylist_sections=stylist_sections,
    )
    if isinstance(prompt_result, Failure):
        return Failure(prompt_result.failure())
    sheet_prompt = prompt_result.unwrap()

    edit_result = img.edit(
        sheet_prompt,
        refs=[style_ref_path],
        size=config.image_provider.default_size,
        quality=config.image_provider.quality,
        model=config.image_provider.model,
    )
    if isinstance(edit_result, Failure):
        return Failure(edit_result.failure())
    sheet_bytes = edit_result.unwrap()

    save_result = save_bytes(
        run_dir / "assets" / "locations" / f"{parsed.id}.png",
        sheet_bytes,
    )
    if isinstance(save_result, Failure):
        return Failure(save_result.failure())
    sheet_path = save_result.unwrap()

    return Success(
        Location(
            id=parsed.id,
            name=parsed.name,
            description=parsed.description,
            sheet_path=sheet_path,
        )
    )


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

    # Bind locally so pyright keeps the non-None narrowing across the
    # later `replace(state, ...)` rebinding (which widens state.stylist
    # back to Optional in the type-checker's view).
    stylist = state.stylist
    layer = config.layers.location

    text_prompt_result = prompt_loader.render(
        TEXT_TEMPLATE,
        mpbv=state.mpbv.raw_markdown,
        stylist=stylist.raw_markdown,
        max_locations=config.limits.max_locations,
    )
    if isinstance(text_prompt_result, Failure):
        return Failure(text_prompt_result.failure())
    text_prompt = text_prompt_result.unwrap()

    text_result = llm.complete(
        text_prompt,
        model=layer.model,
        temperature=layer.temperature,
        max_tokens=layer.max_tokens,
        thinking=layer.thinking,
        reasoning_effort=layer.reasoning_effort,
    )
    if isinstance(text_result, Failure):
        logger.error("layer_failed", layer="location", phase="text", error=text_result.failure().message)
        return Failure(text_result.failure())

    raw_markdown = text_result.unwrap()
    # Cache + persist raw LLM output before any image call so resume can
    # skip the stochastic LLM phase even if the layer returns Failure
    # later. plan §3.8.
    state = replace(state, location_markdown=raw_markdown)
    cache_save = save_state(state, state_path_for(run_dir, "location"))
    if isinstance(cache_save, Failure):
        return Failure(cache_save.failure())

    parsed_result = parse_location_markdown(raw_markdown)
    if isinstance(parsed_result, Failure):
        return Failure(parsed_result.failure())
    parsed_locs = parsed_result.unwrap()

    # PoC 2026-05-24: relaxed from fatal to warn-only — mirrors the same
    # change in `layers/character.py`. The cap was killing runs when MPBV
    # over-proposed (especially same-place angle variants before the
    # location prompt fix); user-facing impact (run abort) was much worse
    # than the per-location $0.21 cost the cap was meant to defend.
    if len(parsed_locs) > config.limits.max_locations:
        logger.warning(
            "location_count_exceeded",
            count=len(parsed_locs),
            limit=config.limits.max_locations,
            message=(
                "LLM proposed more locations than max_locations limit; "
                "generating all and continuing"
            ),
        )

    # Preflight: every parsed location must expose `### 視覚的特徴` before any
    # image call. Otherwise a malformed late block burns image API calls
    # on earlier ones and leaves orphaned `_v002.png` sheets behind.
    for parsed in parsed_locs:
        if not extract_subsection(parsed.description, "視覚的特徴").strip():
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"location {parsed.id!r} missing 視覚的特徴 section",
                    detail={"location_id": parsed.id},
                )
            )

    locations: list[Location] = []
    for parsed in parsed_locs:
        sheet_result = _render_sheet(
            parsed,
            stylist.style_ref_path,
            stylist.raw_markdown,
            prompt_loader,
            img,
            config,
            run_dir,
        )
        if isinstance(sheet_result, Failure):
            logger.error(
                "location_sheet_failed",
                location_id=parsed.id,
                error=sheet_result.failure().message,
            )
            return Failure(sheet_result.failure())
        locations.append(sheet_result.unwrap())

    new_state = replace(state, locations=locations)
    logger.info("layer_completed", layer="location", count=len(locations))
    return Success(new_state)


__all__ = ["generate_location_layer"]
