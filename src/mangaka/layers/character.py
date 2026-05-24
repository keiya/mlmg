"""Character layer: MPBV + Stylist → list[Character] + per-char sheet PNGs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mangaka.config import MangakaConfig
from mangaka.domain import Character, MangaState
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.assets import save_bytes
from mangaka.image.client import ImageClient
from mangaka.image.sections import SECTION_SETS, extract_sections
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.parse.character import ParsedCharacter, parse_character_markdown
from mangaka.parse.sections import extract_subsection
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

TEXT_TEMPLATE = "05_character.md"
IMAGE_TEMPLATE = "05b_character_sheet.md"


def _render_sheet(
    parsed: ParsedCharacter,
    style_ref_path: Path,
    stylist_md: str,
    prompt_loader: PromptLoader,
    img: ImageClient,
    config: MangakaConfig,
    run_dir: Path,
) -> Result[Character, MangaError]:
    """Generate one character's sheet PNG and assemble the `Character` domain object."""
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
        run_dir / "assets" / "characters" / f"{parsed.id}.png",
        sheet_bytes,
    )
    if isinstance(save_result, Failure):
        return Failure(save_result.failure())
    sheet_path = save_result.unwrap()

    return Success(
        Character(
            id=parsed.id,
            name=parsed.name,
            description=parsed.description,
            sheet_paths=[sheet_path],
        )
    )


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

    layer = config.layers.character

    text_prompt_result = prompt_loader.render(
        TEXT_TEMPLATE,
        mpbv=state.mpbv.raw_markdown,
        stylist=state.stylist.raw_markdown,
        max_main_characters=config.limits.max_main_characters,
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
        logger.error("layer_failed", layer="character", phase="text", error=text_result.failure().message)
        return Failure(text_result.failure())

    parsed_result = parse_character_markdown(text_result.unwrap())
    if isinstance(parsed_result, Failure):
        return Failure(parsed_result.failure())
    parsed_chars = parsed_result.unwrap()

    # PoC 2026-05-24: relaxed from fatal to warn-only. The previous strict
    # cap killed the entire run when MPBV proposed more than max characters
    # (it often inflates with voice/concept/group entities that aren't real
    # "main" characters — see prompts/05_character.md). The user's concern
    # was the abort, not the per-char $0.21 cost. The character prompt
    # itself discourages over-counting; if it slips through, we still
    # generate all proposed characters and let the run continue.
    if len(parsed_chars) > config.limits.max_main_characters:
        logger.warning(
            "character_count_exceeded",
            count=len(parsed_chars),
            limit=config.limits.max_main_characters,
            message=(
                "LLM proposed more characters than max_main_characters limit; "
                "generating all and continuing"
            ),
        )

    # Preflight: every parsed character must expose a `### 外見` subsection
    # BEFORE we spend an image call on any of them. Otherwise a malformed
    # late block leaves orphaned `_v002.png` sheets for earlier blocks on
    # disk and burns paid API calls.
    for parsed in parsed_chars:
        if not extract_subsection(parsed.description, "外見").strip():
            return Failure(
                MangaError(
                    kind=ErrorKind.PARSE_ERROR,
                    message=f"character {parsed.id!r} missing 外見 section",
                    detail={"character_id": parsed.id},
                )
            )

    characters: list[Character] = []
    for parsed in parsed_chars:
        sheet_result = _render_sheet(
            parsed,
            state.stylist.style_ref_path,
            state.stylist.raw_markdown,
            prompt_loader,
            img,
            config,
            run_dir,
        )
        if isinstance(sheet_result, Failure):
            logger.error(
                "character_sheet_failed",
                character_id=parsed.id,
                error=sheet_result.failure().message,
            )
            return Failure(sheet_result.failure())
        characters.append(sheet_result.unwrap())

    new_state = replace(state, characters=characters)
    logger.info("layer_completed", layer="character", count=len(characters))
    return Success(new_state)


__all__ = ["generate_character_layer"]
