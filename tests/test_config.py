"""Tests for `mangaka.config.load_config` and validation rules."""

from __future__ import annotations

import textwrap
from pathlib import Path

from returns.result import Failure, Success

from mangaka.config import MangakaConfig, load_config
from mangaka.errors import ErrorKind

_DEFAULT_LAYERS_TOML = textwrap.dedent(
    """
    [layers.plot]
    model = "gpt-5.4-mini"
    temperature = 1.0
    max_tokens = 48000
    thinking = false

    [layers.backstory]
    model = "gpt-5.4-mini"
    temperature = 0.9
    max_tokens = 48000
    thinking = false

    [layers.mpbv]
    model = "gpt-5.4"
    temperature = 0.7
    max_tokens = 64000
    thinking = true
    reasoning_effort = "high"

    [layers.stylist]
    model = "gpt-5.4-mini"
    temperature = 0.7
    max_tokens = 8192
    thinking = false

    [layers.character]
    model = "gpt-5.4-mini"
    temperature = 1.0
    max_tokens = 16000
    thinking = false

    [layers.location]
    model = "gpt-5.4-mini"
    temperature = 0.9
    max_tokens = 16000
    thinking = false

    [layers.page_plan]
    model = "gpt-5.4"
    temperature = 0.7
    max_tokens = 16000
    thinking = true
    reasoning_effort = "medium"
    """
).lstrip()


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_config_defaults(tmp_path: Path) -> None:
    p = _write_config(tmp_path, _DEFAULT_LAYERS_TOML)
    result = load_config(p)
    assert isinstance(result, Success)
    cfg: MangakaConfig = result.unwrap()
    assert cfg.image.max_refs_per_page == 16
    assert cfg.pdf.binding == "rtl"
    assert cfg.layers.mpbv.reasoning_effort == "high"


def test_legacy_page_beat_layer_silently_dropped(tmp_path: Path) -> None:
    """Old run dirs snapshotted configs containing [layers.page_beat]
    before the layer was removed (commit 71f9119). `mangaka export
    <old_run>` loads that snapshot and would hard-fail on the now-
    forbidden extra key. The before-validator in LayersConfig drops
    page_beat silently so existing runs still export."""
    legacy_extra = textwrap.dedent(
        """
        [layers.page_beat]
        model = "gpt-5.4-mini"
        temperature = 0.7
        max_tokens = 16000
        thinking = false
        """
    )
    p = _write_config(tmp_path, _DEFAULT_LAYERS_TOML + legacy_extra)
    result = load_config(p)
    assert isinstance(result, Success)
    cfg = result.unwrap()
    # Layer is gone from the dataclass — confirm we dropped it, not
    # silently absorbed it as an unknown attribute.
    assert not hasattr(cfg.layers, "page_beat")


def test_pdf_fit_cover_rejected(tmp_path: Path) -> None:
    """Round-5 review fix: `pdf.fit = "cover"` is not implemented in export.

    Used to be silently accepted then ignored. Tightened the Literal to
    `["contain"]` only; re-add "cover" when export_pdf gains a cover branch.
    """
    body = _DEFAULT_LAYERS_TOML + '\n[pdf]\nfit = "cover"\n'
    p = _write_config(tmp_path, body)
    result = load_config(p)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR


def test_max_refs_per_page_below_2_rejected(tmp_path: Path) -> None:
    body = _DEFAULT_LAYERS_TOML + "\n[image]\nmax_refs_per_page = 1\n"
    p = _write_config(tmp_path, body)
    result = load_config(p)
    assert isinstance(result, Failure)
    err = result.failure()
    assert err.kind == ErrorKind.CONFIG_ERROR


def test_include_prev_page_ref_with_parallel_workers_rejected(tmp_path: Path) -> None:
    """plan §3.6 / commit (d): parallel page_render pre-builds refs before
    any worker runs, so include_prev_page_ref would silently lose the
    prev-page image. Loud-fail at config load instead."""
    body = (
        _DEFAULT_LAYERS_TOML
        + "\n[image]\ninclude_prev_page_ref = true\n"
        + "\n[concurrency]\nimage_workers = 4\n"
    )
    p = _write_config(tmp_path, body)
    result = load_config(p)
    assert isinstance(result, Failure)
    err = result.failure()
    assert err.kind == ErrorKind.CONFIG_ERROR
    assert "include_prev_page_ref" in err.message


def test_include_prev_page_ref_with_workers_1_accepted(tmp_path: Path) -> None:
    """Drop to image_workers=1 if you genuinely need prev_page refs."""
    body = (
        _DEFAULT_LAYERS_TOML
        + "\n[image]\ninclude_prev_page_ref = true\n"
        + "\n[concurrency]\nimage_workers = 1\n"
    )
    p = _write_config(tmp_path, body)
    result = load_config(p)
    assert isinstance(result, Success)


def test_warn_above_hard_limit_rejected(tmp_path: Path) -> None:
    body = _DEFAULT_LAYERS_TOML + "\n[image]\nmax_prompt_chars = 10000\nwarn_prompt_chars = 20000\n"
    p = _write_config(tmp_path, body)
    result = load_config(p)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR


def test_thinking_without_reasoning_effort_rejected(tmp_path: Path) -> None:
    """thinking=True must come with an explicit reasoning_effort.

    Otherwise the LLM client falls back to OpenAI defaults and we lose the
    intended "high" reasoning on MPBV / "medium" on PagePlan silently.
    """
    body = _DEFAULT_LAYERS_TOML.replace(
        '[layers.mpbv]\nmodel = "gpt-5.4"\ntemperature = 0.7\nmax_tokens = 64000\nthinking = true\nreasoning_effort = "high"',
        '[layers.mpbv]\nmodel = "gpt-5.4"\ntemperature = 0.7\nmax_tokens = 64000\nthinking = true',
    )
    p = _write_config(tmp_path, body)
    result = load_config(p)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR


def test_reasoning_effort_without_thinking_rejected(tmp_path: Path) -> None:
    body = _DEFAULT_LAYERS_TOML.replace(
        '[layers.plot]\nmodel = "gpt-5.4-mini"\ntemperature = 1.0\nmax_tokens = 48000\nthinking = false',
        '[layers.plot]\nmodel = "gpt-5.4-mini"\ntemperature = 1.0\nmax_tokens = 48000\nthinking = false\nreasoning_effort = "high"',
    )
    p = _write_config(tmp_path, body)
    result = load_config(p)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR


def test_missing_file_returns_io_error(tmp_path: Path) -> None:
    result = load_config(tmp_path / "missing.toml")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.IO_ERROR


def test_invalid_toml_returns_config_error(tmp_path: Path) -> None:
    p = _write_config(tmp_path, "this is not valid toml = [\n")
    result = load_config(p)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR
