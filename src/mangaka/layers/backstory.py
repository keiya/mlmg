"""Backstory layer: seed + plot → `Backstories`."""

from __future__ import annotations

from dataclasses import replace

from mangaka.config import MangakaConfig
from mangaka.domain import Backstories, MangaState
from mangaka.errors import ErrorKind, MangaError
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

TEMPLATE_NAME = "02_backstory.md"


def generate_backstories(
    state: MangaState,
    llm: LLMClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
) -> Result[MangaState, MangaError]:
    """Generate the world bible from seed + master plot."""
    logger.info("layer_started", layer="backstory")

    if state.master_plot is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="backstory layer requires master_plot",
                detail={"missing": "master_plot"},
            )
        )

    layer = config.layers.backstory

    prompt_result = prompt_loader.render(
        TEMPLATE_NAME,
        seed_input=state.seed_input,
        master_plot=state.master_plot.raw_markdown,
    )
    if isinstance(prompt_result, Failure):
        return Failure(prompt_result.failure())
    prompt = prompt_result.unwrap()

    response_result = llm.complete(
        prompt,
        model=layer.model,
        temperature=layer.temperature,
        max_tokens=layer.max_tokens,
        thinking=layer.thinking,
        reasoning_effort=layer.reasoning_effort,
    )
    if isinstance(response_result, Failure):
        logger.error("layer_failed", layer="backstory", error=response_result.failure().message)
        return Failure(response_result.failure())
    response_text = response_result.unwrap()

    new_state = replace(state, backstories=Backstories(raw_markdown=response_text))
    logger.info("layer_completed", layer="backstory", output_chars=len(response_text))
    return Success(new_state)


__all__ = ["generate_backstories"]
