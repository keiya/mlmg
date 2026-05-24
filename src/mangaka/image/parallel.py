"""Parallel image-job executor.

Stateless thread-pool wrapper around `ImageClient`. Each `ImageJob` is a
self-contained unit: prompt + refs → bytes → save to deterministic path.
The executor itself owns no `MangaState` — callers reduce the result list
into domain objects on the main thread, typically via the `on_complete`
callback.

Concurrency invariants (§3.5 of docs/plans/parallel_image_generation.md):

- Workers never touch `MangaState`.
- Workers never share mutable data with each other.
- `on_complete` is called serially on the main thread in completion order
  — safe to use for state checkpointing without locks.
- Distinct `ImageJob.output_path` per batch is the caller's responsibility
  (`save_bytes_strict` is atomic, so a same-target collision surfaces as
  FILE_EXISTS Failure on N-1 workers; but distinct paths is the contract).

Drain protocol under `fail_fast` (§3.6):

- `ThreadPoolExecutor.cancel()` on a future that's already running is a
  no-op. So on first failure we cancel pending (queued) futures, but
  continue iterating `as_completed`. In-flight workers complete naturally
  and their successful outcomes flow through `on_complete` — guaranteeing
  no PNG hits disk without state catching up to reference it.
- Cancelled futures surface from `as_completed` and are filtered out via
  `fut.cancelled()`. Their jobs never executed; no side-effect.
- `first_error` = earliest-completing failure. Deterministic given a
  fixed worker count + scheduler.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from mangaka.errors import ErrorKind, MangaError
from mangaka.image.assets import save_bytes_strict
from mangaka.image.client import ImageClient
from mangaka.logging import get_logger
from mangaka.result import Failure, Result, Success

# Type alias: each worker future resolves to a Result[Path, MangaError].
_JobFuture = Future[Result[Path, MangaError]]

logger = get_logger(__name__)


@dataclass(frozen=True)
class ImageJob:
    """A self-contained, deterministic image-generation unit.

    `id` is for logging only — never parsed for routing. Callers thread
    domain objects through via `tag` so `on_complete` can recover the
    source `PageOutline` / `ParsedCharacter` / `ParsedLocation` without
    stringly-typed parsing of `id`.

    `output_path` is the canonical write target. Workers call
    `save_bytes_strict(output_path, bytes)`, which fails loudly if the
    target already exists. Callers must enqueue jobs with distinct
    `output_path` within a single batch.
    """

    id: str
    prompt: str
    refs: list[Path]
    output_path: Path
    size: str
    quality: str
    model: str
    tag: object = None


@dataclass(frozen=True)
class ImageJobOutcome:
    """One job's terminal state: either a saved path or a typed error."""

    job: ImageJob
    result: Result[Path, MangaError]


def _render_one(job: ImageJob, img: ImageClient) -> Result[Path, MangaError]:
    """Worker body: API call → strict save → return saved path."""
    edit_result = img.edit(
        job.prompt,
        refs=job.refs,
        size=job.size,
        quality=job.quality,
        model=job.model,
    )
    if isinstance(edit_result, Failure):
        return Failure(edit_result.failure())
    return save_bytes_strict(job.output_path, edit_result.unwrap())


def _cancel_pending(futures: dict[_JobFuture, ImageJob]) -> None:
    """Best-effort cancel: only futures that haven't started will obey."""
    for fut in futures:
        fut.cancel()


def _coerce_worker_outcome(
    fut: _JobFuture, job: ImageJob
) -> Result[Path, MangaError]:
    """Pull a worker's Result, wrapping any escaped Exception as Failure.

    Workers should always return Result. An exception here is a programmer
    bug — surface as a typed Failure so the drain path runs. Narrowed to
    `Exception` (not `BaseException`) so KeyboardInterrupt / SystemExit /
    asyncio CancelledError propagate as control flow.
    """
    try:
        return fut.result()
    except Exception as exc:
        return Failure(
            MangaError(
                kind=ErrorKind.INVALID_STATE,
                message=(
                    f"worker for job {job.id!r} raised "
                    f"{type(exc).__name__}: {exc}"
                ),
                detail={"job_id": job.id},
            )
        )


def _invoke_on_complete(
    outcome: ImageJobOutcome,
    on_complete: Callable[[ImageJobOutcome], Result[None, MangaError]],
) -> MangaError | None:
    """Call `on_complete` and convert both Failure return and any raised
    exception into the same Failure-shaped signal. Returning None means
    the callback succeeded.

    Wrapping caller exceptions matters: if `on_complete` raises (e.g. a
    KeyError on tag lookup, or `next(...)` StopIteration), letting that
    escape the as_completed loop would skip the cancel + drain path and
    leave in-flight workers writing PNGs without their callbacks ever
    running → state-vs-disk orphans.
    """
    try:
        cb_result = on_complete(outcome)
    except Exception as exc:
        return MangaError(
            kind=ErrorKind.INVALID_STATE,
            message=(
                f"on_complete for job {outcome.job.id!r} raised "
                f"{type(exc).__name__}: {exc}"
            ),
            detail={"job_id": outcome.job.id},
        )
    if isinstance(cb_result, Failure):
        return cb_result.failure()
    return None


def run_image_jobs(
    jobs: Sequence[ImageJob],
    img: ImageClient,
    *,
    max_workers: int,
    on_complete: Callable[[ImageJobOutcome], Result[None, MangaError]] | None = None,
    fail_fast: bool = True,
) -> Result[list[ImageJobOutcome], MangaError]:
    """Run image jobs concurrently and aggregate outcomes.

    See module docstring for the full design. Returns `Failure(first_error)`
    on `fail_fast=True` if any job or `on_complete` fails. Returns
    `Success(outcomes)` otherwise; outcomes are in **completion order**
    (not submission order), and callers that need a canonical order
    should re-sort by `outcome.job.tag` or similar.

    Empty `jobs` returns `Success([])` immediately (no executor created).
    """
    if not jobs:
        return Success([])

    first_error: MangaError | None = None
    outcomes: list[ImageJobOutcome] = []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures: dict[_JobFuture, ImageJob] = {
            ex.submit(_render_one, job, img): job for job in jobs
        }

        for fut in as_completed(futures):
            job = futures[fut]
            # Cancelled futures never ran — no side-effect, skip.
            if fut.cancelled():
                continue

            worker_result = _coerce_worker_outcome(fut, job)
            outcome = ImageJobOutcome(job=job, result=worker_result)
            outcomes.append(outcome)

            step_err: MangaError | None
            if isinstance(worker_result, Failure):
                step_err = worker_result.failure()
                logger.warning(
                    "image_job_failed", job_id=job.id, error=step_err.message
                )
            elif on_complete is None:
                logger.info("image_job_completed", job_id=job.id)
                step_err = None
            else:
                step_err = _invoke_on_complete(outcome, on_complete)
                if step_err is None:
                    logger.info("image_job_completed", job_id=job.id)
                else:
                    logger.warning(
                        "image_job_on_complete_failed",
                        job_id=job.id,
                        error=step_err.message,
                    )

            if step_err is not None:
                if first_error is None:
                    first_error = step_err
                if fail_fast:
                    _cancel_pending(futures)

    if first_error is not None and fail_fast:
        return Failure(first_error)
    return Success(outcomes)


__all__ = ["ImageJob", "ImageJobOutcome", "run_image_jobs"]
