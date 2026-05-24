"""PageRender layer: per-page image generation directly from PagePlan.

For each `PagePlan.page_outline[N]`:
1. Initialize `state.pages` with one `Page` per outline (preserves
   existing pages with `image_path` on resume).
2. Build refs (style + chars + loc) and the Japanese prompt for each
   page that hasn't been rendered yet.
3. Submit all jobs to `run_image_jobs` (parallel; bounded by
   `config.concurrency.image_workers`).
4. `on_complete` (main-thread, serial) updates the matching `Page` in
   state and saves a checkpoint after every completion — partial
   failures don't discard paid renders.

Parallelization safety:
- This layer pre-builds every job's refs BEFORE any worker runs, so
  page N's prompt cannot see page N-1's freshly-rendered image.
  `MangakaConfig` validates that `include_prev_page_ref=True` is
  incompatible with `image_workers > 1` and loud-fails at config load.
- Per-page jobs target distinct `pages/page_NNN.png` paths by
  construction; `save_bytes_strict` is atomic so accidental collision
  surfaces as `IO_ERROR/FILE_EXISTS`, not silent overwrite.

> **Design note (PoC 2026-05-24)**: this layer used to consume `PageBeat`
> (per-panel structured directives) but PageBeat was removed when PoC
> showed gpt-image-2 produces stronger narrative pages when given the
> page_outline semantics directly. See `docs/ARCHITECTURE.md` 設計の進化.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

from mangaka.config import MangakaConfig
from mangaka.domain import MangaState, Page, PageOutline
from mangaka.errors import ErrorKind, MangaError
from mangaka.image.client import ImageClient
from mangaka.image.parallel import ImageJob, ImageJobOutcome, run_image_jobs
from mangaka.image.prompts import build_page_prompt
from mangaka.image.ref_builder import build_refs
from mangaka.llm.client import LLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.logging import get_logger
from mangaka.persistence import save_state, state_path_for
from mangaka.result import Failure, Result, Success

logger = get_logger(__name__)


def _build_jobs(
    state: MangaState,
    outlines: list[PageOutline],
    config: MangakaConfig,
    run_dir: Path,
) -> Result[list[ImageJob], MangaError]:
    """Build one ImageJob per outline that still needs rendering.

    Prompt assembly failures abort the whole batch before any image call
    — same atomic-pre-flight discipline as the character / location
    layers. The pre-flight catches things like `max_prompt_chars` budget
    violations; we'd rather see that loud-fail than half-render a run.
    """
    rendered = {p.page_number for p in state.pages if p.image_path is not None}
    jobs: list[ImageJob] = []
    for outline in outlines:
        if outline.page_number in rendered:
            continue
        labeled_refs = build_refs(
            state,
            outline,
            max_refs=config.image.max_refs_per_page,
            include_prev=config.image.include_prev_page_ref,
        )
        prompt_result = build_page_prompt(state, outline, labeled_refs, config)
        if isinstance(prompt_result, Failure):
            return Failure(prompt_result.failure())
        jobs.append(
            ImageJob(
                id=f"page_{outline.page_number:03d}",
                prompt=prompt_result.unwrap(),
                refs=[r.path for r in labeled_refs],
                output_path=run_dir / "pages" / f"page_{outline.page_number:03d}.png",
                size=config.image_provider.default_size,
                quality=config.image_provider.quality,
                model=config.image_provider.model,
                tag=outline,
            )
        )
    return Success(jobs)


def generate_page_render_layer(
    state: MangaState,
    llm: LLMClient,  # part of the ImageLayerFn signature, unused here
    img: ImageClient,
    config: MangakaConfig,
    prompt_loader: PromptLoader,  # same — unused
    *,
    run_dir: Path,
) -> Result[MangaState, MangaError]:
    """Render every page in `state.page_plan.page_outline` not yet rendered.

    Parallelism bounded by `config.concurrency.image_workers`. Per-page
    state checkpoints land in `state_09_page_render.json` after every
    successful completion, so resume picks up exactly where the last
    paid render finished.
    """
    _ = (llm, prompt_loader)
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

    # Initialize state.pages from page_plan.page_outline. Preserve any
    # existing Page (with image_path) to support resume.
    existing_by_number = {p.page_number: p for p in state.pages}
    initialized: list[Page] = []
    outlines_sorted = sorted(
        state.page_plan.page_outline, key=lambda o: o.page_number
    )
    for outline in outlines_sorted:
        existing = existing_by_number.get(outline.page_number)
        if existing is not None:
            initialized.append(existing)
        else:
            initialized.append(Page(page_number=outline.page_number, image_path=None))
    current = replace(state, pages=initialized)

    jobs_result = _build_jobs(current, outlines_sorted, config, run_dir)
    if isinstance(jobs_result, Failure):
        return Failure(jobs_result.failure())
    jobs = jobs_result.unwrap()
    if not jobs:
        logger.info("layer_completed", layer="page_render", rendered=len(current.pages))
        return Success(current)

    checkpoint_path = state_path_for(run_dir, "page_render")

    # Main-thread state mutation — workers never touch `current`. The
    # outcome's tag carries the PageOutline this job was built from, so
    # we don't have to parse `job.id` back to a page_number.
    def on_complete(outcome: ImageJobOutcome) -> Result[None, MangaError]:
        nonlocal current
        outline = cast("PageOutline", outcome.job.tag)
        # Worker-side Failure already routed through the executor's
        # first_error path; on_complete only fires on Success.
        if isinstance(outcome.result, Failure):
            return Failure(outcome.result.failure())
        saved_path = outcome.result.unwrap()
        idx = next(
            i for i, p in enumerate(current.pages)
            if p.page_number == outline.page_number
        )
        new_pages = [*current.pages]
        new_pages[idx] = replace(new_pages[idx], image_path=saved_path)
        current = replace(current, pages=new_pages)
        logger.info(
            "page_render_completed",
            page_number=outline.page_number,
            image_path=str(saved_path),
        )
        return save_state(current, checkpoint_path)

    exec_result = run_image_jobs(
        jobs,
        img,
        max_workers=config.concurrency.image_workers,
        on_complete=on_complete,
        fail_fast=True,
    )
    if isinstance(exec_result, Failure):
        return Failure(exec_result.failure())

    logger.info(
        "layer_completed",
        layer="page_render",
        rendered=sum(1 for p in current.pages if p.image_path is not None),
    )
    return Success(current)


__all__ = ["generate_page_render_layer"]
