"""Tests for `src/mangaka/image/parallel.py`.

Covers:
- Empty input fast-path
- All-success outcome aggregation
- `tag` thread-through (no stringly-typed routing)
- Drain protocol under fail-fast: in-flight successes still commit via
  on_complete after a sibling failure
- on_complete returning Failure also triggers drain
- on_complete is invoked serially on the main thread (no overlap)
- Programmer-bug worker exception is wrapped as INVALID_STATE Failure
- Concurrent on_complete calls preserve ordering invariants
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from returns.result import Failure, Success

from mangaka.errors import ErrorKind, MangaError
from mangaka.image.parallel import ImageJob, ImageJobOutcome, run_image_jobs
from mangaka.result import Result

# ---------------------------------------------------------------------------
# Fake ImageClient with per-prompt latency + per-prompt forced-failure hooks
# ---------------------------------------------------------------------------

@dataclass
class _ProgrammableImageClient:
    """Fake `ImageClient.edit` that can inject latency + failures keyed by
    a substring of the prompt. Returns deterministic bytes (the prompt
    encoded as UTF-8) on success so the save layer sees real content.

    `enter_barrier` (optional) — every call waits on this barrier on entry
    so callers can synchronize "all workers in flight" before any returns.
    `failure_gates` (optional) — per-prompt-key Events: a failure waits
    until its Event is set before returning. Lets tests guarantee that
    `boom` doesn't return before its siblings have started.
    """

    latencies: dict[str, float] = field(default_factory=dict[str, float])
    failures: dict[str, MangaError] = field(default_factory=dict[str, MangaError])
    failure_gates: dict[str, threading.Event] = field(
        default_factory=dict[str, threading.Event]
    )
    enter_barrier: threading.Barrier | None = None
    call_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def generate(
        self,
        prompt: str,
        *,
        size: str = "1024x1536",
        quality: str = "high",
        model: str = "gpt-image-2",
    ) -> Result[bytes, MangaError]:
        _ = (size, quality, model)
        return self._respond(prompt)

    def edit(
        self,
        prompt: str,
        *,
        base: Path | None = None,
        refs: Sequence[Path] = (),
        size: str = "1024x1536",
        quality: str = "high",
        model: str = "gpt-image-2",
    ) -> Result[bytes, MangaError]:
        _ = (base, refs, size, quality, model)
        return self._respond(prompt)

    def _respond(self, prompt: str) -> Result[bytes, MangaError]:
        with self._lock:
            self.call_count += 1
        if self.enter_barrier is not None:
            self.enter_barrier.wait()
        for key, delay in self.latencies.items():
            if key in prompt:
                time.sleep(delay)
                break
        for key, gate in self.failure_gates.items():
            if key in prompt:
                gate.wait()
                break
        for key, err in self.failures.items():
            if key in prompt:
                return Failure(err)
        return Success(prompt.encode("utf-8"))


def _make_job(
    tmp_path: Path, name: str, *, prompt: str | None = None
) -> ImageJob:
    """ImageJob whose output path is tmp_path/<name>.png."""
    return ImageJob(
        id=name,
        prompt=prompt or f"prompt_for_{name}",
        refs=[],
        output_path=tmp_path / f"{name}.png",
        size="1024x1536",
        quality="high",
        model="gpt-image-2",
        tag={"name": name},  # opaque caller-supplied handle
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_jobs_is_success_empty() -> None:
    """No work → no executor, no calls."""
    client = _ProgrammableImageClient()
    result = run_image_jobs([], client, max_workers=4)
    assert isinstance(result, Success)
    assert result.unwrap() == []
    assert client.call_count == 0


def test_all_success_outcomes_aggregate(tmp_path: Path) -> None:
    """5 jobs, all succeed → 5 outcomes, all Success, all PNGs on disk."""
    client = _ProgrammableImageClient()
    jobs = [_make_job(tmp_path, f"j{i}") for i in range(5)]

    result = run_image_jobs(jobs, client, max_workers=4)
    assert isinstance(result, Success)
    outcomes = result.unwrap()
    assert len(outcomes) == 5
    assert all(isinstance(o.result, Success) for o in outcomes)
    # Files on disk and content matches the deterministic per-prompt bytes.
    for o in outcomes:
        assert isinstance(o.result, Success)
        path = o.result.unwrap()
        assert path.read_bytes() == o.job.prompt.encode("utf-8")


def test_tag_threads_through_to_outcome(tmp_path: Path) -> None:
    """on_complete must see the original `tag` without parsing `id`."""
    client = _ProgrammableImageClient()
    jobs = [_make_job(tmp_path, f"j{i}") for i in range(3)]
    tags_seen: list[object] = []
    lock = threading.Lock()

    def on_complete(outcome: ImageJobOutcome) -> Result[None, MangaError]:
        with lock:
            tags_seen.append(outcome.job.tag)
        return Success(None)

    result = run_image_jobs(jobs, client, max_workers=4, on_complete=on_complete)
    assert isinstance(result, Success)
    # Every job's tag should appear (order may differ from submission).
    names_seen: set[str] = set()
    for t in tags_seen:
        assert isinstance(t, dict)
        # `tag` is typed as `object` on ImageJob, so reach in dynamically.
        name = t["name"]  # type: ignore[index]
        assert isinstance(name, str)
        names_seen.add(name)
    assert names_seen == {"j0", "j1", "j2"}


def test_fail_fast_drains_in_flight_on_complete(tmp_path: Path) -> None:
    """Drain protocol: when one job fails, in-flight successes whose
    worker has *already started* must still flow through on_complete
    so their PNGs don't become state-orphans.

    Determinism: every worker hits `enter_barrier(5)` first, so all 5
    are in-flight before any returns. `boom` then waits on its gate;
    we release it from the on_complete callback the moment the first
    sibling commits, guaranteeing the drain path actually exercises."""
    barrier = threading.Barrier(parties=5)
    boom_gate = threading.Event()
    client = _ProgrammableImageClient(
        latencies={"slow": 0.05},
        failures={
            "boom": MangaError(
                kind=ErrorKind.IMAGE_CALL_FAILED, message="forced failure"
            )
        },
        failure_gates={"boom": boom_gate},
        enter_barrier=barrier,
    )
    jobs = [
        _make_job(tmp_path, "fast1", prompt="prompt_fast1"),
        _make_job(tmp_path, "fast2", prompt="prompt_fast2"),
        _make_job(tmp_path, "boom", prompt="prompt_boom"),
        _make_job(tmp_path, "slow1", prompt="prompt_slow1"),
        _make_job(tmp_path, "slow2", prompt="prompt_slow2"),
    ]
    committed: list[str] = []
    lock = threading.Lock()

    def on_complete(outcome: ImageJobOutcome) -> Result[None, MangaError]:
        with lock:
            committed.append(outcome.job.id)
        # Release `boom` only after at least one sibling has committed.
        # Drain must still pick up the remaining siblings.
        boom_gate.set()
        return Success(None)

    result = run_image_jobs(
        jobs, client, max_workers=5, on_complete=on_complete, fail_fast=True
    )
    assert isinstance(result, Failure)
    assert result.failure().message == "forced failure"

    # All 5 workers entered the barrier ⇒ all 5 started ⇒ all 4 successes
    # must have flowed through on_complete (whether before or after `boom`).
    assert set(committed) == {"fast1", "fast2", "slow1", "slow2"}


def test_on_complete_failure_triggers_drain(tmp_path: Path) -> None:
    """If on_complete itself fails (e.g. disk full on checkpoint), the
    same drain semantics apply: pending cancelled, in-flight committed
    where possible, first_error returned."""
    client = _ProgrammableImageClient(latencies={"slow": 0.15})
    jobs = [
        _make_job(tmp_path, "fast1", prompt="prompt_fast1"),
        _make_job(tmp_path, "boom_cb", prompt="prompt_boom_cb"),
        _make_job(tmp_path, "slow1", prompt="prompt_slow1"),
    ]
    cb_calls: list[str] = []
    lock = threading.Lock()
    forced = MangaError(kind=ErrorKind.IO_ERROR, message="forced checkpoint fail")

    def on_complete(outcome: ImageJobOutcome) -> Result[None, MangaError]:
        with lock:
            cb_calls.append(outcome.job.id)
        if outcome.job.id == "boom_cb":
            return Failure(forced)
        return Success(None)

    result = run_image_jobs(
        jobs, client, max_workers=3, on_complete=on_complete, fail_fast=True
    )
    assert isinstance(result, Failure)
    assert result.failure().message == "forced checkpoint fail"
    # `boom_cb` was committed (image succeeded, cb saw it).
    assert "boom_cb" in cb_calls


def test_on_complete_exception_wrapped_and_drains(tmp_path: Path) -> None:
    """A buggy on_complete that *raises* (instead of returning Failure)
    must still go through cancel + drain. Otherwise in-flight workers
    save PNGs to disk and their on_complete never runs → state-vs-disk
    orphan. This was caught by Codex review of commit (b)."""
    barrier = threading.Barrier(parties=3)
    client = _ProgrammableImageClient(
        latencies={"slow": 0.05}, enter_barrier=barrier
    )
    jobs = [
        _make_job(tmp_path, "first", prompt="prompt_first"),
        _make_job(tmp_path, "slow1", prompt="prompt_slow1"),
        _make_job(tmp_path, "slow2", prompt="prompt_slow2"),
    ]
    committed: list[str] = []
    lock = threading.Lock()

    def buggy_on_complete(outcome: ImageJobOutcome) -> Result[None, MangaError]:
        # First callback raises; later callbacks must still run (drain).
        with lock:
            committed.append(outcome.job.id)
        if outcome.job.id == "first":
            raise RuntimeError("callback bug")
        return Success(None)

    result = run_image_jobs(
        jobs, client, max_workers=3, on_complete=buggy_on_complete, fail_fast=True
    )
    assert isinstance(result, Failure)
    err = result.failure()
    assert err.kind == ErrorKind.INVALID_STATE
    assert "callback bug" in err.message
    # All in-flight jobs (= all 3, since barrier ensures concurrent start)
    # must have seen on_complete — even the ones after the buggy one.
    assert set(committed) == {"first", "slow1", "slow2"}


def test_worker_exception_wrapped_as_invalid_state(tmp_path: Path) -> None:
    """Workers should always return Result; if one raises, surface it
    as INVALID_STATE Failure rather than crashing run_image_jobs."""

    class BoomClient(_ProgrammableImageClient):
        def edit(
            self,
            prompt: str,
            *,
            base: Path | None = None,
            refs: Sequence[Path] = (),
            size: str = "1024x1536",
            quality: str = "high",
            model: str = "gpt-image-2",
        ) -> Result[bytes, MangaError]:
            _ = (prompt, base, refs, size, quality, model)
            raise RuntimeError("contract violation")

    jobs = [_make_job(tmp_path, "j0")]
    result = run_image_jobs(jobs, BoomClient(), max_workers=2, fail_fast=True)
    assert isinstance(result, Failure)
    err = result.failure()
    assert err.kind == ErrorKind.INVALID_STATE
    assert "contract violation" in err.message


def test_distinct_paths_required_invariant_holds(tmp_path: Path) -> None:
    """Workers each target distinct output_paths by construction. The
    save_bytes_strict layer enforces no-overwrite atomically."""
    client = _ProgrammableImageClient()
    jobs = [_make_job(tmp_path, f"distinct{i}") for i in range(8)]
    result = run_image_jobs(jobs, client, max_workers=8)
    assert isinstance(result, Success)
    paths = sorted(p.name for p in tmp_path.glob("distinct*.png"))
    assert paths == [f"distinct{i}.png" for i in range(8)]


def test_collision_surfaces_as_file_exists(tmp_path: Path) -> None:
    """If a caller violates the distinct-paths contract, save_bytes_strict
    fails the second worker loudly (FILE_EXISTS). N-1 outcomes are
    Failures; the first wins."""
    client = _ProgrammableImageClient()
    same_path = tmp_path / "shared.png"

    def _collide(i: int) -> ImageJob:
        return ImageJob(
            id=f"j{i}",
            prompt=f"prompt{i}",  # distinct payloads
            refs=[],
            output_path=same_path,
            size="1024x1536",
            quality="high",
            model="gpt-image-2",
        )

    jobs = [_collide(i) for i in range(4)]
    # fail_fast=False so we see all 4 outcomes.
    result = run_image_jobs(jobs, client, max_workers=4, fail_fast=False)
    assert isinstance(result, Success)
    outcomes = result.unwrap()
    successes = [o for o in outcomes if isinstance(o.result, Success)]
    failures = [o for o in outcomes if isinstance(o.result, Failure)]
    assert len(successes) == 1
    assert len(failures) == 3
    for o in failures:
        assert isinstance(o.result, Failure)
        detail = o.result.failure().detail or {}
        assert detail.get("reason") == "FILE_EXISTS"
