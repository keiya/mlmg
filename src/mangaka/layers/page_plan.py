"""PagePlan layer: MPBV + Stylist + Characters + Locations → `PagePlan`.

The LLM returns a single JSON object covering both the arc decomposition and
the per-page outline. All SCHEMA §6 cross-field invariants are enforced by
`parse_page_plan_text` before the new state is returned.
"""

from __future__ import annotations

from dataclasses import replace

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState
from mangaka.errors import ErrorKind, MangaError
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.parse.page_plan import parse_page_plan_text
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)

TEMPLATE_NAME = "07_page_plan.md"


def _format_id_block(items: list[tuple[str, str]]) -> str:
    """Render `[(id, name), ...]` as a bulleted list for the prompt."""
    if not items:
        return "(なし)"
    return "\n".join(f"- `{i}` — {n}" for i, n in items)


def generate_page_plan(
    state: MangaState,
    llm: LLMClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
) -> Result[MangaState, MangaError]:
    """Generate the PagePlan JSON and validate it against SCHEMA §6."""
    logger.info("layer_started", layer="page_plan")

    if state.mpbv is None or state.stylist is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="page_plan layer requires mpbv and stylist",
                detail={
                    "have_mpbv": state.mpbv is not None,
                    "have_stylist": state.stylist is not None,
                },
            )
        )
    if not state.characters:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="page_plan layer requires at least one character",
            )
        )
    if not state.locations:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="page_plan layer requires at least one location",
            )
        )

    layer = config.layers.page_plan

    prompt_result = prompt_loader.render(
        TEMPLATE_NAME,
        mpbv=state.mpbv.raw_markdown,
        stylist=state.stylist.raw_markdown,
        character_ids_block=_format_id_block([(c.id, c.name) for c in state.characters]),
        location_ids_block=_format_id_block([(loc.id, loc.name) for loc in state.locations]),
        max_pages=config.limits.max_pages,
        max_arc_phases=config.limits.max_arc_phases,
    )
    if isinstance(prompt_result, Failure):
        return Failure(prompt_result.failure())
    prompt = prompt_result.unwrap()

    # Honor `limits.max_parse_retries`: a single LLM formatting drift
    # shouldn't abort the whole pipeline. We re-prompt with the validator
    # error appended so the model can self-correct, then either succeed
    # within budget or surface the LAST parse failure.
    max_attempts = config.limits.max_parse_retries + 1
    last_error: MangaError | None = None
    current_prompt = prompt
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
            # LLM-side failures (rate-limit / API errors) are NOT parse retries —
            # they're already retried inside the client. Surface immediately.
            logger.error(
                "layer_failed",
                layer="page_plan",
                error=response_result.failure().message,
            )
            return Failure(response_result.failure())

        parsed = parse_page_plan_text(
            response_result.unwrap(),
            max_pages=config.limits.max_pages,
            max_arc_phases=config.limits.max_arc_phases,
            known_character_ids=[c.id for c in state.characters],
            known_location_ids=[loc.id for loc in state.locations],
        )
        if isinstance(parsed, Success):
            new_state = replace(state, page_plan=parsed.unwrap())
            logger.info(
                "layer_completed",
                layer="page_plan",
                total_pages=parsed.unwrap().total_pages,
                arc_phases=len(parsed.unwrap().arc),
                parse_attempts=attempt + 1,
            )
            return Success(new_state)

        last_error = parsed.failure()
        logger.warning(
            "page_plan_parse_failed",
            attempt=attempt + 1,
            max_attempts=max_attempts,
            error=last_error.message,
        )
        # Append validator feedback so the next response can self-correct.
        current_prompt = (
            f"{prompt}\n\n"
            "# 直前の出力の検証結果\n"
            "前回の出力は以下の理由で却下されました。**全く同じ JSON は出力せず**、"
            "下記指摘を必ず反映した上で再生成してください:\n\n"
            f"- {last_error.message}"
        )

    # Exhausted retries — surface the last validation error.
    assert last_error is not None  # invariant: loop ran at least once
    logger.error(
        "page_plan_parse_exhausted",
        attempts=max_attempts,
        error=last_error.message,
    )
    return Failure(last_error)


__all__ = ["generate_page_plan"]
