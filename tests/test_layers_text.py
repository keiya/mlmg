"""Tests for the three text-only layers: plot, backstory, mpbv.

All driven by `FakeLLMClient`; no real API calls.
"""

from __future__ import annotations

from dataclasses import replace

from _helpers import make_test_config, prompts_dir
from returns.result import Failure, Success

from mangaka.domain import Backstories, MangaState, MasterPlot
from mangaka.errors import ErrorKind, MangaError
from mangaka.layers.backstory import generate_backstories
from mangaka.layers.mpbv import generate_mpbv
from mangaka.layers.plot import generate_master_plot
from mangaka.llm.client_fake import FakeLLMClient
from mangaka.llm.prompts import PromptLoader


def _initial_state() -> MangaState:
    return MangaState(seed_input="魔法学校に通う少年の短編", run_name="t")


def test_plot_layer_populates_master_plot() -> None:
    state = _initial_state()
    llm = FakeLLMClient(default_response="# Master Plot\n本文")
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    result = generate_master_plot(state, llm, config, loader)
    assert isinstance(result, Success)
    new_state = result.unwrap()
    assert new_state.master_plot is not None
    assert new_state.master_plot.raw_markdown == "# Master Plot\n本文"


def test_plot_layer_uses_layer_config_for_temperature_and_model() -> None:
    state = _initial_state()
    llm = FakeLLMClient(default_response="x")
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    result = generate_master_plot(state, llm, config, loader)
    assert isinstance(result, Success)
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call.model == config.layers.plot.model
    assert call.temperature == config.layers.plot.temperature
    assert call.max_tokens == config.layers.plot.max_tokens


def test_plot_template_includes_seed_and_max_pages() -> None:
    """Smoke: the rendered prompt should expose seed text and max_pages.

    Catches silent breakage if someone renames the template's variable hooks.
    """
    state = _initial_state()
    llm = FakeLLMClient(default_response="x")
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    generate_master_plot(state, llm, config, loader)
    rendered = llm.calls[0].prompt
    assert "魔法学校" in rendered
    assert str(config.limits.max_pages) in rendered


def test_backstory_requires_master_plot() -> None:
    state = _initial_state()  # no master_plot set
    llm = FakeLLMClient()
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    result = generate_backstories(state, llm, config, loader)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE
    assert len(llm.calls) == 0  # short-circuit, no LLM call


def test_backstory_layer_passes_plot_text_to_template() -> None:
    state = replace(_initial_state(), master_plot=MasterPlot(raw_markdown="PLOT_TEXT"))
    llm = FakeLLMClient(default_response="# Backstories\n世界")
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    result = generate_backstories(state, llm, config, loader)
    assert isinstance(result, Success)
    assert "PLOT_TEXT" in llm.calls[0].prompt
    assert result.unwrap().backstories is not None


def test_mpbv_requires_both_inputs() -> None:
    only_plot = replace(_initial_state(), master_plot=MasterPlot(raw_markdown="x"))
    llm = FakeLLMClient()
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    result = generate_mpbv(only_plot, llm, config, loader)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_mpbv_layer_uses_thinking_config() -> None:
    state = replace(
        _initial_state(),
        master_plot=MasterPlot(raw_markdown="P"),
        backstories=Backstories(raw_markdown="B"),
    )
    llm = FakeLLMClient(default_response="# Master Plot\nfinal")
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    result = generate_mpbv(state, llm, config, loader)
    assert isinstance(result, Success)
    call = llm.calls[0]
    assert call.thinking is True
    assert call.reasoning_effort == "high"


def test_layer_propagates_llm_failure() -> None:
    state = _initial_state()
    err = MangaError(kind=ErrorKind.LLM_CALL_FAILED, message="boom")
    llm = FakeLLMClient().with_failure(err)
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    result = generate_master_plot(state, llm, config, loader)
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.LLM_CALL_FAILED
