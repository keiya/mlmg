"""Shared pytest fixtures for mangaka.

Layer-specific Fake clients arrive in M1 / M2. For now this file just exposes
a sample run-name fixture used by smoke tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def sample_run_name() -> str:
    return "smoke_run"


@pytest.fixture
def tmp_runs_dir(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    return runs
