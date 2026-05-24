"""Stylist layer: MPBV → `Stylist` (Markdown guide + `style.png`).

Two sub-steps:
1. LLM generates a 10-section Markdown guide from MPBV.
2. ImageClient.generate produces a style reference PNG from the visual
   sections (4 / 5 / 6 / 10).

Stylist runs before Character / Location because both downstream layers
need `style_ref_path` as an image ref.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState, Stylist
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.assets import save_bytes
from mangaka.image.client import ImageClient
from mangaka.image.sections import SECTION_SETS, extract_sections, missing_sections
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

TEXT_TEMPLATE = "04_stylist.md"
IMAGE_TEMPLATE = "04b_style_ref.md"


def generate_stylist_layer(
    state: MangaState,
    llm: LLMClient,
    img: ImageClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
    *,
    run_dir: Path,
) -> Result[MangaState, MangaError]:
    """Produce a Stylist guide + style reference image."""
    logger.info("layer_started", layer="stylist")

    if state.mpbv is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="stylist layer requires mpbv",
                detail={"missing": "mpbv"},
            )
        )

    layer = config.layers.stylist

    # Step 1: text guide
    prompt_result = prompt_loader.render(TEXT_TEMPLATE, mpbv=state.mpbv.raw_markdown)
    if isinstance(prompt_result, Failure):
        return Failure(prompt_result.failure())
    text_prompt = prompt_result.unwrap()

    text_result = llm.complete(
        text_prompt,
        model=layer.model,
        temperature=layer.temperature,
        max_tokens=layer.max_tokens,
        thinking=layer.thinking,
        reasoning_effort=layer.reasoning_effort,
    )
    if isinstance(text_result, Failure):
        logger.error("layer_failed", layer="stylist", phase="text", error=text_result.failure().message)
        return Failure(text_result.failure())
    raw_markdown = text_result.unwrap()

    # Step 2: validate the COMPLETE 10-section guide before persisting.
    # Stylist text is the upstream source for character/location/page-beat/
    # page-render prompts — each pulls a different SECTION_SETS subset. If a
    # section outside style_ref's own subset (e.g. section 7 for char design)
    # is missing now, the downstream `extract_sections` will silently produce
    # empty guidance for that layer instead of triggering regeneration.
    required = list(range(1, 11))  # sections 1..10 (per docs/SCHEMA.md §3)
    missing = missing_sections(raw_markdown, required)
    if missing:
        return Failure(
            MangaError(
                kind=ErrorKind.PARSE_ERROR,
                message=(
                    f"stylist text missing required sections {missing} "
                    "(expected all of 1..10)"
                ),
                detail={"missing_sections": missing},
            )
        )
    sections = extract_sections(raw_markdown, SECTION_SETS["style_ref"])

    image_prompt_result = prompt_loader.render(IMAGE_TEMPLATE, stylist_sections=sections)
    if isinstance(image_prompt_result, Failure):
        return Failure(image_prompt_result.failure())
    image_prompt = image_prompt_result.unwrap()

    image_result = img.generate(
        image_prompt,
        size=config.image_provider.default_size,
        quality=config.image_provider.quality,
        model=config.image_provider.model,
    )
    if isinstance(image_result, Failure):
        logger.error("layer_failed", layer="stylist", phase="image", error=image_result.failure().message)
        return Failure(image_result.failure())
    image_bytes = image_result.unwrap()

    save_result = save_bytes(run_dir / "assets" / "style.png", image_bytes)
    if isinstance(save_result, Failure):
        return Failure(save_result.failure())
    style_ref_path = save_result.unwrap()

    new_state = replace(
        state,
        stylist=Stylist(raw_markdown=raw_markdown, style_ref_path=style_ref_path),
    )
    logger.info(
        "layer_completed",
        layer="stylist",
        text_chars=len(raw_markdown),
        style_ref=str(style_ref_path),
    )
    return Success(new_state)


__all__ = ["generate_stylist_layer"]
