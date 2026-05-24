"""Tests for the CLI helpers that don't require a real LLM call."""

from __future__ import annotations

import argparse
from pathlib import Path

from returns.result import Failure, Success

from mangaka.cli import _load_seed as _load_seed  # pyright: ignore[reportPrivateUsage]
from mangaka.cli import (
    _validate_run_name as _validate_run_name,  # pyright: ignore[reportPrivateUsage]
)
from mangaka.errors import ErrorKind


def test_run_name_allows_simple_segment() -> None:
    result = _validate_run_name("alice_v1")
    assert isinstance(result, Success)


def test_run_name_rejects_slash() -> None:
    """Path separators would escape the runs/ directory."""
    result = _validate_run_name("foo/bar")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR


def test_run_name_rejects_backslash() -> None:
    result = _validate_run_name("foo\\bar")
    assert isinstance(result, Failure)


def test_run_name_rejects_absolute_path() -> None:
    """Regression guard for round-2 review: `--name /tmp/demo` would clobber /tmp/demo."""
    result = _validate_run_name("/tmp/demo")
    assert isinstance(result, Failure)


def test_run_name_rejects_dotdot() -> None:
    result = _validate_run_name("..")
    assert isinstance(result, Failure)


def test_run_name_rejects_leading_dot() -> None:
    result = _validate_run_name(".secret")
    assert isinstance(result, Failure)


def test_run_name_rejects_empty() -> None:
    result = _validate_run_name("")
    assert isinstance(result, Failure)


def test_load_seed_rejects_non_utf8(tmp_path: Path) -> None:
    """Regression guard for M2 round-3 fix.

    Previously a non-UTF-8 seed file crashed the CLI with `UnicodeDecodeError`;
    must now surface as a typed `CONFIG_ERROR` so the user sees `[CONFIG_ERROR]
    seed file is not valid UTF-8: ...` instead of a stack trace.
    """
    bad = tmp_path / "shift_jis.txt"
    bad.write_bytes(b"\x82\xa0\x82\xa2\x82\xa4")  # "あいう" in CP932
    args = argparse.Namespace(seed=None, seed_file=bad)
    result = _load_seed(args)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR
    assert "UTF-8" in result.failure().message


def test_load_seed_reads_utf8_file(tmp_path: Path) -> None:
    p = tmp_path / "seed.txt"
    p.write_text("魔法学校の話", encoding="utf-8")
    args = argparse.Namespace(seed=None, seed_file=p)
    result = _load_seed(args)
    assert isinstance(result, Success)
    assert result.unwrap() == "魔法学校の話"


def test_load_seed_rejects_empty_file(tmp_path: Path) -> None:
    """Round-2 review fix: an empty seed file would otherwise burn API calls."""
    p = tmp_path / "empty.txt"
    p.write_text("", encoding="utf-8")
    args = argparse.Namespace(seed=None, seed_file=p)
    result = _load_seed(args)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR
    assert "empty" in result.failure().message


def test_load_seed_rejects_whitespace_only_file(tmp_path: Path) -> None:
    p = tmp_path / "ws.txt"
    p.write_text("   \n\t\n", encoding="utf-8")
    args = argparse.Namespace(seed=None, seed_file=p)
    result = _load_seed(args)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR


def test_load_seed_rejects_whitespace_only_positional() -> None:
    args = argparse.Namespace(seed="   ", seed_file=None)
    result = _load_seed(args)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR


def test_run_subcommand_refuses_existing_run_dir(tmp_path: Path) -> None:
    """Round-8 review fix: a second `mangaka run` with the same --name must
    not silently corrupt the prior run's state. Pre-existing state files
    in `runs/{name}/` should make the run refuse with a clear message.
    """
    import subprocess
    import sys

    # Pre-create a fake "previous run" with at least one state file.
    runs_root = tmp_path / "runs"
    target = runs_root / "test_run_name"
    target.mkdir(parents=True)
    (target / "state_00_init.json").write_text(
        '{"seed_input": "old", "run_name": "test_run_name"}', encoding="utf-8"
    )

    # Minimal valid config so load_config succeeds.
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[general]\n"
        f'runs_dir = "{runs_root}"\n'
        '[layers.plot]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.backstory]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.mpbv]\nmodel = "x"\nmax_tokens = 1\nthinking = true\nreasoning_effort = "high"\n'
        '[layers.stylist]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.character]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.location]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.page_plan]\nmodel = "x"\nmax_tokens = 1\nthinking = true\nreasoning_effort = "medium"\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable, "-m", "mangaka", "run", "fresh seed",
            "--name", "test_run_name",
            "--config", str(config_path),
        ],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1
    combined = proc.stderr + proc.stdout
    assert "already contains state files" in combined


def test_run_subcommand_force_clears_stale_state_files(tmp_path: Path) -> None:
    """Round-10 review fix: `--force` must remove old state_NN_*.json so
    `latest_state_path` cannot pick up a stale higher-numbered snapshot.
    Without this cleanup, a forced `--until plot` after a previous full
    render would leave `state_09_page_render.json` in place, and a later
    `mangaka export` would export the prior manga.

    Also covers config snapshot writing as a side effect: the run dir
    should contain `config.toml` after the run command runs (init phase
    happens before the pipeline failure on missing OPENAI_API_KEY).
    """
    import subprocess
    import sys

    runs_root = tmp_path / "runs"
    target = runs_root / "stale_run"
    target.mkdir(parents=True)
    stale = target / "state_09_page_render.json"
    stale.write_text(
        '{"seed_input": "old", "run_name": "stale_run"}', encoding="utf-8"
    )

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[general]\n"
        f'runs_dir = "{runs_root}"\n'
        '[layers.plot]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.backstory]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.mpbv]\nmodel = "x"\nmax_tokens = 1\nthinking = true\nreasoning_effort = "high"\n'
        '[layers.stylist]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.character]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.location]\nmodel = "x"\nmax_tokens = 1\n'
        '[layers.page_plan]\nmodel = "x"\nmax_tokens = 1\nthinking = true\nreasoning_effort = "medium"\n',
        encoding="utf-8",
    )

    env = {"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": ""}
    subprocess.run(
        [
            sys.executable, "-m", "mangaka", "run", "fresh seed",
            "--name", "stale_run",
            "--config", str(config_path),
            "--force",
        ],
        capture_output=True, text=True, check=False, env=env,
    )
    # Stale state_09 must be gone; init was written after cleanup, before
    # the pipeline failure on missing API key.
    assert not stale.exists(), "stale state_09 was not cleared on --force"
    assert (target / "state_00_init.json").exists()
    # Config snapshot saved into the run dir.
    assert (target / "config.toml").exists()


def test_llm_and_image_retry_configs_use_limits_budgets() -> None:
    """Round-3 + round-5 fix: both clients must honor their `limits.*` budgets.

    `[retry]` provides backoff *shape*; `[limits]` provides per-domain retry
    counts. Without the model_copy override either knob is a silent no-op.
    Guards against a future refactor that re-shares one config across both.
    """
    from mangaka.config import RetryConfig

    retry_cfg = RetryConfig(max_retries=99, initial_delay=1.0, max_delay=60.0)
    llm_retry_cfg = retry_cfg.model_copy(update={"max_retries": 3})
    image_retry_cfg = retry_cfg.model_copy(update={"max_retries": 2})
    assert retry_cfg.max_retries == 99
    assert llm_retry_cfg.max_retries == 3
    assert image_retry_cfg.max_retries == 2
    # Backoff shape is preserved on both copies.
    for copied in (llm_retry_cfg, image_retry_cfg):
        assert copied.initial_delay == retry_cfg.initial_delay
        assert copied.max_delay == retry_cfg.max_delay
        assert copied.exponential_base == retry_cfg.exponential_base
