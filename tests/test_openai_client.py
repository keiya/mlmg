"""Behavioural tests for `OpenAILLMClient` that don't hit the network.

Real-API smoke is covered by integration tests under `pytest -m integration`
(RUN_INTEGRATION=1).
"""

from __future__ import annotations

import httpx
import pytest
from openai import APIStatusError
from returns.result import Failure

from mangaka.config import RetryConfig
from mangaka.errors import ErrorKind
from mangaka.llm.client_openai import OpenAILLMClient


def test_missing_api_key_returns_config_error_not_runtime_error(
    monkeypatch: object,
) -> None:
    """A missing OPENAI_API_KEY must surface as a typed Failure, not a crash.

    Regression guard for the M1 review fix: previously the `client` property
    raised `RuntimeError`, which would escape through the retry handler and
    crash the CLI instead of following the Result discipline.
    """
    # type: ignore[attr-defined] — pytest's monkeypatch typing isn't strict
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # type: ignore[attr-defined]

    client = OpenAILLMClient(
        default_model="test-model",
        retry_config=RetryConfig(max_retries=0, initial_delay=0.0, max_delay=0.0),
        api_key=None,
    )
    result = client.complete("hi", model="test-model")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.CONFIG_ERROR


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_4xx_classified_as_non_retryable_bad_request(
    status_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4xx must be `LLM_BAD_REQUEST` so the retry handler short-circuits.

    Regression guard for round-2 review: invalid API key / model / params used
    to be wrapped as `LLM_CALL_FAILED` (retryable), causing every bad config
    to sleep through every backoff attempt before failing.
    """
    client = OpenAILLMClient(
        default_model="m",
        retry_config=RetryConfig(max_retries=2, initial_delay=0.0, max_delay=0.0),
        api_key="sk-fake",
    )

    def _raise_4xx(**_: object) -> object:
        response = httpx.Response(
            status_code=status_code,
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        )
        raise APIStatusError(
            f"injected {status_code}", response=response, body=None
        )

    monkeypatch.setattr(client._client.responses, "create", _raise_4xx)  # type: ignore[union-attr]

    result = client.complete("hi", model="m")
    assert isinstance(result, Failure)
    err = result.failure()
    assert err.kind == ErrorKind.LLM_BAD_REQUEST
    assert err.detail is not None
    assert err.detail.get("status_code") == status_code


def test_5xx_remains_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx / server-side errors remain `LLM_CALL_FAILED` (retryable)."""
    client = OpenAILLMClient(
        default_model="m",
        retry_config=RetryConfig(max_retries=0, initial_delay=0.0, max_delay=0.0),
        api_key="sk-fake",
    )

    def _raise_5xx(**_: object) -> object:
        response = httpx.Response(
            status_code=502,
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        )
        raise APIStatusError("injected 502", response=response, body=None)

    monkeypatch.setattr(client._client.responses, "create", _raise_5xx)  # type: ignore[union-attr]

    result = client.complete("hi", model="m")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.LLM_CALL_FAILED


def test_sdk_internal_retries_disabled() -> None:
    """Round-4 fix: SDK-level retries must be disabled so RetryHandler is the
    only retry layer. Otherwise default `max_retries=2` (SDK) stacked under
    `RetryHandler(max_retries=3)` produces up to 12 attempts per call.
    """
    client = OpenAILLMClient(
        default_model="m",
        retry_config=RetryConfig(max_retries=3, initial_delay=0.0, max_delay=0.0),
        api_key="sk-fake",
    )
    assert client._client is not None  # pyright: ignore[reportPrivateUsage]
    assert client._client.max_retries == 0  # pyright: ignore[reportPrivateUsage]


def test_incomplete_response_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-4 fix: `status='incomplete'` Responses must NOT be returned as
    Success. The partial output_text could be a truncated plot/backstory/mpbv
    and persisting it would silently corrupt downstream layers.
    """
    from types import SimpleNamespace

    client = OpenAILLMClient(
        default_model="m",
        retry_config=RetryConfig(max_retries=0, initial_delay=0.0, max_delay=0.0),
        api_key="sk-fake",
    )

    def _return_incomplete(**_: object) -> object:
        return SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output_text="partial truncated content",
            usage=None,
        )

    monkeypatch.setattr(client._client.responses, "create", _return_incomplete)  # type: ignore[union-attr]

    result = client.complete("hi", model="m")
    assert isinstance(result, Failure)
    err = result.failure()
    assert err.kind == ErrorKind.LLM_CALL_FAILED
    assert err.detail is not None
    assert err.detail.get("status") == "incomplete"
    assert err.detail.get("incomplete_reason") == "max_output_tokens"
