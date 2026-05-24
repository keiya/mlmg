"""Tests for the PagePlan layer (text-only) using Fake LLM."""

from __future__ import annotations

import textwrap
from dataclasses import replace
from pathlib import Path

from _helpers import make_test_config, prompts_dir
from returns.result import Failure, Success

from mangaka.domain import (
    MPBV,
    Backstories,
    Character,
    Location,
    MangaState,
    MasterPlot,
    Stylist,
)
from mangaka.errors import ErrorKind
from mangaka.layers.page_plan import generate_page_plan
from mangaka.llm.client_fake import FakeLLMClient
from mangaka.llm.prompts import PromptLoader

_VALID_PLAN_JSON = textwrap.dedent(
    """\
    ```json
    {
      "total_pages": 2,
      "arc": [
        {"phase": "起", "start_page": 1, "end_page": 2, "summary": "導入"}
      ],
      "page_outline": [
        {"page_number": 1, "phase": "起", "summary": "屋上で考え事",
         "character_ids": ["alice"], "location_id": "rooftop"},
        {"page_number": 2, "phase": "起", "summary": "ボブも合流",
         "character_ids": ["alice", "bob"], "location_id": "rooftop"}
      ]
    }
    ```
    """
)


def _state_with_prereqs(tmp_path: Path) -> MangaState:
    style_ref = tmp_path / "assets" / "style.png"
    style_ref.parent.mkdir(parents=True, exist_ok=True)
    style_ref.write_bytes(b"x")
    return MangaState(
        seed_input="seed",
        run_name="t",
        master_plot=MasterPlot(raw_markdown="plot"),
        backstories=Backstories(raw_markdown="bs"),
        mpbv=MPBV(raw_markdown="mpbv"),
        stylist=Stylist(raw_markdown="style", style_ref_path=style_ref),
        characters=[
            Character(id="alice", name="アリス", description="...", sheet_paths=[tmp_path / "a.png"]),
            Character(id="bob", name="ボブ", description="...", sheet_paths=[tmp_path / "b.png"]),
        ],
        locations=[
            Location(id="rooftop", name="屋上", description="...", sheet_path=tmp_path / "r.png"),
        ],
    )


def test_page_plan_layer_happy_path(tmp_path: Path) -> None:
    state = _state_with_prereqs(tmp_path)
    llm = FakeLLMClient(default_response=_VALID_PLAN_JSON)
    config = make_test_config()
    loader = PromptLoader(prompts_dir())

    result = generate_page_plan(state, llm, config, loader)
    assert isinstance(result, Success)
    new_state = result.unwrap()
    assert new_state.page_plan is not None
    assert new_state.page_plan.total_pages == 2
    assert len(new_state.page_plan.arc) == 1
    assert len(new_state.page_plan.page_outline) == 2


def test_page_plan_requires_mpbv(tmp_path: Path) -> None:
    state = MangaState(seed_input="s", run_name="t")
    result = generate_page_plan(
        state, FakeLLMClient(default_response=_VALID_PLAN_JSON),
        make_test_config(), PromptLoader(prompts_dir()),
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_page_plan_requires_characters(tmp_path: Path) -> None:
    state = replace(_state_with_prereqs(tmp_path), characters=[])
    result = generate_page_plan(
        state, FakeLLMClient(default_response=_VALID_PLAN_JSON),
        make_test_config(), PromptLoader(prompts_dir()),
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.MISSING_PREREQUISITE


def test_page_plan_requires_locations(tmp_path: Path) -> None:
    state = replace(_state_with_prereqs(tmp_path), locations=[])
    result = generate_page_plan(
        state, FakeLLMClient(default_response=_VALID_PLAN_JSON),
        make_test_config(), PromptLoader(prompts_dir()),
    )
    assert isinstance(result, Failure)


def test_page_plan_propagates_llm_failure(tmp_path: Path) -> None:
    from mangaka.errors import MangaError
    state = _state_with_prereqs(tmp_path)
    llm = FakeLLMClient().with_failure(
        MangaError(kind=ErrorKind.LLM_RATE_LIMITED, message="rate-limited")
    )
    result = generate_page_plan(state, llm, make_test_config(), PromptLoader(prompts_dir()))
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.LLM_RATE_LIMITED


def test_page_plan_rejects_invalid_id_reference(tmp_path: Path) -> None:
    """LLM output that references an unknown character/location ID must fail."""
    bad = _VALID_PLAN_JSON.replace('"alice"', '"unknown"', 1)
    state = _state_with_prereqs(tmp_path)
    result = generate_page_plan(
        state,
        FakeLLMClient(default_response=bad),
        make_test_config(),
        PromptLoader(prompts_dir()),
    )
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.VALIDATION_FAILED


def test_page_plan_prompt_includes_known_ids(tmp_path: Path) -> None:
    """Prompt must surface character/location IDs to the LLM."""
    state = _state_with_prereqs(tmp_path)
    llm = FakeLLMClient(default_response=_VALID_PLAN_JSON)
    generate_page_plan(state, llm, make_test_config(), PromptLoader(prompts_dir()))
    prompt = llm.calls[0].prompt
    assert "`alice`" in prompt
    assert "`bob`" in prompt
    assert "`rooftop`" in prompt


def _config_with_parse_retries(retries: int):  # type: ignore[no-untyped-def]
    """MangakaConfig is a Pydantic model; use model_copy, not dataclasses.replace."""
    base = make_test_config()
    return base.model_copy(
        update={"limits": base.limits.model_copy(update={"max_parse_retries": retries})}
    )


def test_page_plan_retries_on_parse_failure(tmp_path: Path) -> None:
    """Round-1 review fix: honor `limits.max_parse_retries`.

    First response is malformed JSON, second is valid. Layer must retry
    instead of aborting on the first failure, and the second prompt must
    include the validator feedback so the LLM can self-correct.
    """
    state = _state_with_prereqs(tmp_path)
    llm = FakeLLMClient(
        responses=["this is not json at all", _VALID_PLAN_JSON],
    )
    config = _config_with_parse_retries(2)
    result = generate_page_plan(state, llm, config, PromptLoader(prompts_dir()))

    assert isinstance(result, Success)
    assert len(llm.calls) == 2
    # Second prompt should include validator feedback ("検証結果").
    second_prompt = llm.calls[1].prompt
    assert "検証結果" in second_prompt
    assert "却下" in second_prompt


def test_page_plan_exhausts_parse_retry_budget(tmp_path: Path) -> None:
    """If every attempt fails, surface the LAST validator failure."""
    state = _state_with_prereqs(tmp_path)
    llm = FakeLLMClient(default_response="not json")
    config = _config_with_parse_retries(2)
    result = generate_page_plan(state, llm, config, PromptLoader(prompts_dir()))

    assert isinstance(result, Failure)
    # 1 initial + 2 retries = 3 attempts.
    assert len(llm.calls) == 3


def test_page_plan_does_not_retry_on_llm_failure(tmp_path: Path) -> None:
    """Parse retries are for validator failures only — LLM call failures
    are already retried inside the client, so the layer must surface them
    on the first attempt without burning the parse retry budget.
    """
    from mangaka.errors import MangaError as _MangaError

    state = _state_with_prereqs(tmp_path)
    llm = FakeLLMClient().with_failure(
        _MangaError(kind=ErrorKind.LLM_RATE_LIMITED, message="rate-limited")
    )
    result = generate_page_plan(state, llm, make_test_config(), PromptLoader(prompts_dir()))

    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.LLM_RATE_LIMITED
    assert len(llm.calls) == 1  # no parse-retry burn on LLM-side failure
