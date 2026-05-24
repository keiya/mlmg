"""Tests for `RetryHandler.calculate_delay` jitter.

End-to-end retry behavior (which exceptions trigger retries, how many
attempts, etc.) is exercised in `test_openai_client.py`. This file
isolates the jitter math.
"""

from __future__ import annotations

from mangaka.config import RetryConfig
from mangaka.llm.retry import RetryHandler


def _handler(*, jitter: float, initial: float = 1.0, base: float = 2.0) -> RetryHandler:
    return RetryHandler(
        RetryConfig(
            max_retries=3,
            initial_delay=initial,
            max_delay=60.0,
            exponential_base=base,
            jitter_ratio=jitter,
        )
    )


def test_zero_jitter_is_deterministic() -> None:
    h = _handler(jitter=0.0)
    # Deterministic exponential backoff: 1.0, 2.0, 4.0.
    assert h.calculate_delay(0) == 1.0
    assert h.calculate_delay(1) == 2.0
    assert h.calculate_delay(2) == 4.0


def test_jitter_bounded_by_ratio() -> None:
    h = _handler(jitter=0.25)
    # 100 samples at attempt=0 should all sit in [0.75, 1.25].
    for _ in range(100):
        d = h.calculate_delay(0)
        assert 0.75 <= d <= 1.25, f"delay {d} out of bounds"


def test_jitter_disperses_across_parallel_threads() -> None:
    """The point of jitter: two threads at the same attempt count should
    almost certainly get different delays. Statistical, but with 50 samples
    the probability of all equal under uniform[0.75, 1.25] is negligible."""
    h = _handler(jitter=0.25)
    samples = [h.calculate_delay(2) for _ in range(50)]
    # At base=2.0, attempt=2: base delay is 4.0, jittered into [3.0, 5.0].
    assert all(3.0 <= s <= 5.0 for s in samples)
    # Should not all be identical (would imply jitter is broken).
    assert len(set(samples)) > 1


def test_jitter_respects_max_delay_before_jittering() -> None:
    """Cap is applied to the deterministic backoff, then jitter is applied —
    so the jittered delay can exceed `max_delay` by up to `jitter_ratio`.
    This is intentional (jitter is small) and documented in the docstring."""
    h = _handler(jitter=0.25, initial=100.0, base=2.0)  # base way above cap
    # capped at 60.0, jittered → [45.0, 75.0]
    samples = [h.calculate_delay(0) for _ in range(50)]
    assert all(45.0 <= s <= 75.0 for s in samples)
    # At least one sample should exceed max_delay (60.0), documenting that
    # jitter is applied AFTER the cap rather than before. With 50 samples
    # uniform in [45, 75], the chance of zero >60 samples is (15/30)^50 ≈ 0.
    assert any(s > 60.0 for s in samples)
