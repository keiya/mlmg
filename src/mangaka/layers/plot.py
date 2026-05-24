"""Plot layer: seed → `MasterPlot`."""

from __future__ import annotations

from dataclasses import replace

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState, MasterPlot
from mangaka.errors import MangaError
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

TEMPLATE_NAME = "01_master_plot.md"


def generate_master_plot(
    state: MangaState,
    llm: LLMClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
) -> Result[MangaState, MangaError]:
    """Generate the master plot from `state.seed_input`."""
    logger.info("layer_started", layer="plot")

    layer = config.layers.plot

    prompt_result = prompt_loader.render(
        TEMPLATE_NAME,
        user_input=state.seed_input,
        max_pages=config.limits.max_pages,
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
        logger.error("layer_failed", layer="plot", error=response_result.failure().message)
        return Failure(response_result.failure())
    response_text = response_result.unwrap()

    new_state = replace(state, master_plot=MasterPlot(raw_markdown=response_text))
    logger.info("layer_completed", layer="plot", output_chars=len(response_text))
    return Success(new_state)


__all__ = ["generate_master_plot"]
