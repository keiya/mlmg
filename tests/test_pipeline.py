"""Tests for `run_pipeline` orchestration."""

from __future__ import annotations

from pathlib import Path

from _helpers import make_test_config, prompts_dir
from returns.result import Failure, Success

from mangaka.domain import MangaState
from mangaka.errors import ErrorKind, MangaError
from mangaka.llm.client_fake import FakeLLMClient
from mangaka.llm.prompts import PromptLoader
from mangaka.pipeline import Until, run_pipeline


def _state() -> MangaState:
    return MangaState(seed_input="短編シード", run_name="t")


def test_run_until_plot_only_runs_one_layer(tmp_path: Path) -> None:
    llm = FakeLLMClient(default_response="# plot output")
    config = make_test_config(runs_dir=str(tmp_path))
    loader = PromptLoader(prompts_dir())

    result = run_pipeline(
        _state(), llm, config, loader, until=Until.PLOT, run_dir=tmp_path
    )
    assert isinstance(result, Success)
    new_state = result.unwrap()
    assert new_state.master_plot is not None
    assert new_state.backstories is None
    assert new_state.mpbv is None

    assert (tmp_path / "state_01_plot.json").exists()
    assert not (tmp_path / "state_02_backstory.json").exists()
    assert not (tmp_path / "state_03_mpbv.json").exists()
    assert len(llm.calls) == 1


def test_run_until_mpbv_runs_all_three_layers(tmp_path: Path) -> None:
    llm = FakeLLMClient(responses=["# plot", "# bs", "# mpbv final"])
    config = make_test_config(runs_dir=str(tmp_path))
    loader = PromptLoader(prompts_dir())

    result = run_pipeline(
        _state(), llm, config, loader, until=Until.MPBV, run_dir=tmp_path
    )
    assert isinstance(result, Success)
    state = result.unwrap()
    assert state.master_plot is not None
    assert state.backstories is not None
    assert state.mpbv is not None
    assert state.mpbv.raw_markdown == "# mpbv final"

    assert (tmp_path / "state_01_plot.json").exists()
    assert (tmp_path / "state_02_backstory.json").exists()
    assert (tmp_path / "state_03_mpbv.json").exists()


def test_failure_in_middle_layer_short_circuits(tmp_path: Path) -> None:
    """If layer N fails, layers N+1.. must not run, and the failure propagates."""
    llm = FakeLLMClient(
        results=[
            Success("# plot ok"),
            Failure(MangaError(kind=ErrorKind.LLM_CALL_FAILED, message="injected")),
        ],
    )
    config = make_test_config(runs_dir=str(tmp_path))
    loader = PromptLoader(prompts_dir())

    result = run_pipeline(
        _state(), llm, config, loader, until=Until.MPBV, run_dir=tmp_path
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.LLM_CALL_FAILED
    assert len(llm.calls) == 2  # plot + backstory, mpbv never called
    assert (tmp_path / "state_01_plot.json").exists()
    assert not (tmp_path / "state_02_backstory.json").exists()
    assert not (tmp_path / "state_03_mpbv.json").exists()


def test_until_backstory_runs_two_layers(tmp_path: Path) -> None:
    llm = FakeLLMClient(responses=["plot text", "bs text"])
    config = make_test_config(runs_dir=str(tmp_path))
    loader = PromptLoader(prompts_dir())

    result = run_pipeline(
        _state(), llm, config, loader, until=Until.BACKSTORY, run_dir=tmp_path
    )
    assert isinstance(result, Success)
    assert result.unwrap().mpbv is None
    assert (tmp_path / "state_02_backstory.json").exists()
    assert not (tmp_path / "state_03_mpbv.json").exists()
    assert len(llm.calls) == 2
