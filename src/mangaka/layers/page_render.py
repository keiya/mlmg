"""PageRender layer: per-page image generation directly from PagePlan.

For each `PagePlan.page_outline[N]`:
1. Initialize `state.pages` with one `Page` per outline (if not yet done).
2. `build_refs(state, page_outline)` — order ref images (style → loc → chars).
3. `build_page_prompt(state, page_outline, refs, config)` — assemble the
   Japanese natural-language prompt (MPBV overview + arc position + page
   outline summary + visuals + stylist + craft directives). Fails with
   `PROMPT_TOO_LONG` if it exceeds `image.max_prompt_chars`.
4. `img.edit(prompt, refs=...)` — gpt-image-2 generates a PNG. Panel layout,
   camera angles, speech bubble placement, narration are decided by the model.
5. Save to `pages/page_NNN.png`; update `Page.image_path` in state.

> **Design note (PoC 2026-05-24)**: this layer used to consume `PageBeat`
> (per-panel structured directives) but PageBeat was removed when PoC showed
> gpt-image-2 produces stronger narrative pages when given the page_outline
> semantics directly. See `docs/ARCHITECTURE.md` 設計の進化.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState, Page, PageOutline
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
    page_outline: PageOutline,
    img: ImageClient,
    config: MangakaConfig,
    run_dir: Path,
) -> Result[Path, MangaError]:
    """Generate the image for a single page; returns the saved path."""
    labeled_refs = build_refs(
        state,
        page_outline,
        max_refs=config.image.max_refs_per_page,
        include_prev=config.image.include_prev_page_ref,
    )

    prompt_result = build_page_prompt(state, page_outline, labeled_refs, config)
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
        run_dir / "pages" / f"page_{page_outline.page_number:03d}.png",
        edit_result.unwrap(),
    )
    if isinstance(save_result, Failure):
        return Failure(save_result.failure())

    saved_path = save_result.unwrap()
    logger.info(
        "page_render_completed",
        page_number=page_outline.page_number,
        image_path=str(saved_path),
        prompt_chars=len(prompt),
        ref_count=len(labeled_refs),
    )
    return Success(saved_path)


def generate_page_render_layer(
    state: MangaState,
    llm: LLMClient,  # part of the ImageLayerFn signature, unused here
    img: ImageClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,  # same — unused
    *,
    run_dir: Path,
) -> Result[MangaState, MangaError]:
    """Render every page in `state.page_plan.page_outline` not yet rendered."""
    _ = (llm, prompt_loader)  # silence ruff/pyright unused-arg without ARG suppressions
    logger.info("layer_started", layer="page_render")

    if state.page_plan is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="page_render requires PagePlan layer first",
            )
        )
    if state.stylist is None:
        return Failure(
            MangaError(
                kind=ErrorKind.MISSING_PREREQUISITE,
                message="page_render requires state.stylist",
            )
        )

    # Initialize state.pages from page_plan.page_outline if empty. This is the
    # first place that needs concrete Page objects (page_plan only stores
    # outlines). Existing pages with image_path are preserved (resume support).
    existing_by_number = {p.page_number: p for p in state.pages}
    initialized: list[Page] = []
    for outline in state.page_plan.page_outline:
        existing = existing_by_number.get(outline.page_number)
        if existing is not None:
            initialized.append(existing)
        else:
            initialized.append(Page(page_number=outline.page_number, image_path=None))
    current = replace(state, pages=initialized)

    # Per-page checkpoint so a mid-loop failure doesn't discard paid renders.
    checkpoint_path = state_path_for(run_dir, "page_render")

    # Render in page-number order (the outline list is already in order but
    # be defensive in case of an injected page_plan edit).
    outlines_sorted = sorted(
        state.page_plan.page_outline, key=lambda o: o.page_number
    )

    for outline in outlines_sorted:
        # Locate the page in current.pages (kept in step with outline order).
        idx = next(
            i for i, p in enumerate(current.pages)
            if p.page_number == outline.page_number
        )
        page = current.pages[idx]
        if page.image_path is not None:
            continue  # already rendered (partial resume)

        render_result = _render_one_page(current, outline, img, config, run_dir)
        if isinstance(render_result, Failure):
            return Failure(render_result.failure())

        new_pages = [*current.pages]
        new_pages[idx] = replace(page, image_path=render_result.unwrap())
        current = replace(current, pages=new_pages)

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
