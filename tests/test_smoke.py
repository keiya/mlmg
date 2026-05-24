"""Smoke tests: import sanity + CLI `--version` and `--help`.

Stop hook runs `pytest -m smoke` before accepting completion of work.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import mangaka


@pytest.mark.smoke
def test_package_has_version() -> None:
    assert isinstance(mangaka.__version__, str)
    assert mangaka.__version__


@pytest.mark.smoke
def test_cli_version_via_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mangaka", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert mangaka.__version__ in result.stdout


@pytest.mark.smoke
def test_cli_help_via_module() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mangaka", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "mangaka" in result.stdout
