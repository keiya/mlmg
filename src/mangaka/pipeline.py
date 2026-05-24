"""Pipeline orchestrator.

`run_pipeline(state, llm, img, config, *, until, run_dir)` executes layers in
canonical order through the requested stop point, persisting state JSON after
each successful layer. Image layers arrive in M2 — for now the `img` parameter
is accepted but only used by layers that need it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.client import ImageClient
from mangaka.layers.backstory import generate_backstories
from mangaka.layers.character import generate_character_layer
from mangaka.layers.location import generate_location_layer
from mangaka.layers.mpbv import generate_mpbv
from mangaka.layers.page_plan import generate_page_plan
from mangaka.layers.page_render import generate_page_render_layer
from mangaka.layers.plot import generate_master_plot
from mangaka.layers.stylist import generate_stylist_layer
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.persistence import save_state, state_path_for
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)


class Until(StrEnum):
    """Stop points for `run_pipeline`."""

    PLOT = "plot"
    BACKSTORY = "backstory"
    MPBV = "mpbv"
    STYLIST = "stylist"
    CHARACTER = "character"
    LOCATION = "location"
    PAGE_PLAN = "page_plan"
    PAGE_RENDER = "page_render"


# Order matters: each entry consumes outputs of all earlier entries.
_LAYER_ORDER: list[Until] = [
    Until.PLOT,
    Until.BACKSTORY,
    Until.MPBV,
    Until.STYLIST,
    Until.CHARACTER,
    Until.LOCATION,
    Until.PAGE_PLAN,
    Until.PAGE_RENDER,
]

TextLayerFn = Callable[
    [MangaState, LLMClient, MangakaConfig, PromptLoader],
    Result[MangaState, MangaError],
]


class ImageLayerFn(Protocol):
    def __call__(
        self,
        state: MangaState,
        llm: LLMClient,
        img: ImageClient,
        config: MangakaConfig,
        prompt_loader: PromptLoader,
        *,
        run_dir: Path,
    ) -> Result[MangaState, MangaError]: ...


@dataclass(frozen=True)
class _LayerSpec:
    name: Until
    state_key: str
    needs_image: bool
    text_fn: TextLayerFn | None = None
    image_fn: ImageLayerFn | None = None


_LAYERS: list[_LayerSpec] = [
    _LayerSpec(name=Until.PLOT, state_key="plot", needs_image=False,
               text_fn=generate_master_plot),
    _LayerSpec(name=Until.BACKSTORY, state_key="backstory", needs_image=False,
               text_fn=generate_backstories),
    _LayerSpec(name=Until.MPBV, state_key="mpbv", needs_image=False,
               text_fn=generate_mpbv),
    _LayerSpec(name=Until.STYLIST, state_key="stylist", needs_image=True,
               image_fn=generate_stylist_layer),
    _LayerSpec(name=Until.CHARACTER, state_key="character", needs_image=True,
               image_fn=generate_character_layer),
    _LayerSpec(name=Until.LOCATION, state_key="location", needs_image=True,
               image_fn=generate_location_layer),
    _LayerSpec(name=Until.PAGE_PLAN, state_key="page_plan", needs_image=False,
               text_fn=generate_page_plan),
    _LayerSpec(name=Until.PAGE_RENDER, state_key="page_render", needs_image=True,
               image_fn=generate_page_render_layer),
]


def _layers_through(until: Until) -> list[_LayerSpec]:
    """Layers to run, in order, up to and including `until`."""
    last_idx = _LAYER_ORDER.index(until)
    target_names = set(_LAYER_ORDER[: last_idx + 1])
    return [layer for layer in _LAYERS if layer.name in target_names]


def run_pipeline(
    state: MangaState,
    llm: LLMClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,
    *,
    until: Until,
    run_dir: Path,
    img: ImageClient | None = None,
) -> Result[MangaState, MangaError]:
    """Run layers in canonical order through `until`, saving state after each.

    `img` is required for any layer with `needs_image=True`. The orchestrator
    returns `MISSING_PREREQUISITE` if it's needed but not supplied.
    """
    if until not in _LAYER_ORDER:
        return Failure(
            MangaError(
                kind=ErrorKind.INVALID_STATE,
                message=f"unknown 'until' layer: {until!r}",
                detail={"until": str(until)},
            )
        )

    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.IO_ERROR,
                message=f"failed to create run dir: {exc}",
                detail={"path": str(run_dir)},
            )
        )

    layers = _layers_through(until)
    if any(spec.needs_image for spec in layers) and img is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message=(
                    "pipeline requires an ImageClient through this stop point — "
                    "pass img=OpenAIImageClient(...) or img=FakeImageClient()"
                ),
                detail={"until": str(until)},
            )
        )

    current = state
    for spec in layers:
        logger.info("pipeline_step", layer=spec.name.value)
        if spec.needs_image:
            assert spec.image_fn is not None
            assert img is not None  # checked above
            step_result = spec.image_fn(
                current, llm, img, config, prompt_loader, run_dir=run_dir
            )
        else:
            assert spec.text_fn is not None
            step_result = spec.text_fn(current, llm, config, prompt_loader)
        if isinstance(step_result, Failure):
            return step_result
        current = step_result.unwrap()

        save_result = save_state(current, state_path_for(run_dir, spec.state_key))
        if isinstance(save_result, Failure):
            return Failure(save_result.failure())

    return Success(current)


__all__ = ["Until", "run_pipeline"]
