"""PageRender layer: per-page image generation.

For each page already populated by `page_beat` layer:
1. `build_refs(state, beat)` — order ref images (`style → loc → prev → chars`).
2. `build_page_prompt(state, beat, refs, config)` — assemble the Japanese
   natural-language prompt; fails with `PROMPT_TOO_LONG` if it exceeds
   `image.max_prompt_chars`.
3. `img.edit(prompt, refs=...)` — gpt-image-2 generates a PNG.
4. Versioned save to `pages/page_NNN.png`; update `Page.image_path` in state.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState, Page
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.assets import save_bytes
from mangaka.image.client import ImageClient
from mangaka.image.prompts import build_page_prompt
from mangaka.image.ref_builder import build_refs
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.persistence import save_state, state_path_for
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)


def _render_one_page(
    state: MangaState,
    page: Page,
    img: ImageClient,
    config: MangakaConfig,
    run_dir: Path,
) -> Result[Page, MangaError]:
    """Generate the image for a single page and update its `image_path`."""
    labeled_refs = build_refs(
        state,
        page.beat,
        max_refs=config.image.max_refs_per_page,
        include_prev=config.image.include_prev_page_ref,
    )

    prompt_result = build_page_prompt(state, page.beat, labeled_refs, config)
    if isinstance(prompt_result, Failure):
        return Failure(prompt_result.failure())
    prompt = prompt_result.unwrap()

    edit_result = img.edit(
        prompt,
        refs=[r.path for r in labeled_refs],
        size=config.image_provider.default_size,
        quality=config.image_provider.quality,
        model=config.image_provider.model,
    )
    if isinstance(edit_result, Failure):
        return Failure(edit_result.failure())

    save_result = save_bytes(
        run_dir / "pages" / f"page_{page.page_number:03d}.png",
        edit_result.unwrap(),
    )
    if isinstance(save_result, Failure):
        return Failure(save_result.failure())

    new_page = replace(page, image_path=save_result.unwrap())
    logger.info(
        "page_render_completed",
        page_number=page.page_number,
        image_path=str(new_page.image_path),
        prompt_chars=len(prompt),
        ref_count=len(labeled_refs),
    )
    return Success(new_page)


def generate_page_render_layer(
    state: MangaState,
    llm: LLMClient,  # part of the ImageLayerFn signature, unused here
    img: ImageClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,  # same — unused
    *,
    run_dir: Path,
) -> Result[MangaState, MangaError]:
    _ = (llm, prompt_loader)  # silence ruff/pyright unused-arg without ARG suppressions
    """Render every page in `state.pages` that doesn't yet have an image_path."""
    logger.info("layer_started", layer="page_render")

    if not state.pages:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="page_render requires PageBeat layer first (state.pages is empty)",
            )
        )
    if state.stylist is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="page_render requires state.stylist",
            )
        )

    # Persist per-page progress: each successful render is checkpointed before
    # the next attempt, so a mid-loop failure doesn't discard already-paid
    # renders. A resume re-reads this state and skips pages whose `image_path`
    # is already set — preventing duplicate paid image calls and orphan PNGs
    # for the rendered tail.
    checkpoint_path = state_path_for(run_dir, "page_render")

    # Render in numeric page-number order regardless of storage order. With
    # `include_prev_page_ref=True`, page N depends on page N-1's `image_path`;
    # if state.pages got reordered (e.g. by a future inject CLI), iterating
    # in storage order could render page 3 before page 2 and miss the prev ref.
    page_order = sorted(
        range(len(state.pages)), key=lambda idx: state.pages[idx].page_number
    )

    current = state
    for i in page_order:
        page = current.pages[i]
        if page.image_path is not None:
            continue  # already rendered (e.g. partial resume)
        page_result = _render_one_page(current, page, img, config, run_dir)
        if isinstance(page_result, Failure):
            return Failure(page_result.failure())
        new_pages = [*current.pages]
        new_pages[i] = page_result.unwrap()
        current = replace(current, pages=new_pages)

        # Checkpoint immediately. If this fails, surface the IO error rather
        # than continuing — a stale state file would mislead a later resume.
        save_result = save_state(current, checkpoint_path)
        if isinstance(save_result, Failure):
            return Failure(save_result.failure())

    logger.info(
        "layer_completed",
        layer="page_render",
        rendered=sum(1 for p in current.pages if p.image_path is not None),
    )
    return Success(current)


__all__ = ["generate_page_render_layer"]
