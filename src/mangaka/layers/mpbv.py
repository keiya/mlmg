"""MPBV layer: plot + backstories → validated `MPBV`.

The validation pass re-derives a consistent master-plot+backstories pair,
resolving contradictions in the raw outputs. This is the highest-reasoning
step in the text-only pipeline; the layer config defaults to `thinking=True
reasoning_effort=high`.
"""

from __future__ import annotations

from dataclasses import replace

from mangaka.config import MangakaConfig
from mangaka.domain import MPBV, MangaState
from mangaka.errors import ErrorKind, MangaError
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

TEMPLATE_NAME = "03_mpbv.md"


def _build_input_block(state: MangaState) -> str:
    """Combine plot + backstories into the single block the MPBV prompt expects."""
    assert state.master_plot is not None
    assert state.backstories is not None
    return (
        "# Master Plot\n\n"
        f"{state.master_plot.raw_markdown}\n\n"
        "# Backstories\n\n"
        f"{state.backstories.raw_markdown}\n"
    )


def generate_mpbv(
    state: MangaState,
    llm: LLMClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
) -> Result[MangaState, MangaError]:
    """Run multi-pass validation of plot + backstories."""
    logger.info("layer_started", layer="mpbv")

    if state.master_plot is None or state.backstories is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="mpbv layer requires both master_plot and backstories",
                detail={
                    "have_master_plot": state.master_plot is not None,
                    "have_backstories": state.backstories is not None,
                },
            )
        )

    layer = config.layers.mpbv

    prompt_result = prompt_loader.render(TEMPLATE_NAME, user_input=_build_input_block(state))
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
        logger.error("layer_failed", layer="mpbv", error=response_result.failure().message)
        return Failure(response_result.failure())
    response_text = response_result.unwrap()

    new_state = replace(state, mpbv=MPBV(raw_markdown=response_text))
    logger.info("layer_completed", layer="mpbv", output_chars=len(response_text))
    return Success(new_state)


__all__ = ["generate_mpbv"]
