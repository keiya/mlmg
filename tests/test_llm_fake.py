"""Tests for `FakeLLMClient`."""

from __future__ import annotations

from returns.result import Failure, Success

from mangaka.errors import ErrorKind, MangaError
from mangaka.llm.client_fake import FakeLLMClient


def test_default_response() -> None:
    client = FakeLLMClient(default_response="hi")
    result = client.complete("prompt", model="gpt-test")
    assert isinstance(result, Success)
    assert result.unwrap() == "hi"


def test_responses_list_consumed_in_order() -> None:
    client = FakeLLMClient(default_response="fallback", responses=["a", "b"])
    assert isinstance(client.complete("p", model="m"), Success)
    assert isinstance(client.complete("p", model="m"), Success)
    third = client.complete("p", model="m")
    assert isinstance(third, Success)
    assert third.unwrap() == "fallback"


def test_calls_recorded_with_full_params() -> None:
    client = FakeLLMClient()
    client.complete(
        "prompt body",
        model="gpt-5.4",
        temperature=0.5,
        max_tokens=2048,
        thinking=True,
        reasoning_effort="high",
    )
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call.prompt == "prompt body"
    assert call.model == "gpt-5.4"
    assert call.temperature == 0.5
    assert call.max_tokens == 2048
    assert call.thinking is True
    assert call.reasoning_effort == "high"


def test_responder_callback() -> None:
    client = FakeLLMClient(responder=lambda p: f"echo:{p}")
    result = client.complete("hello", model="m")
    assert isinstance(result, Success)
    assert result.unwrap() == "echo:hello"


def test_with_failure_returns_failure_variant() -> None:
    err = MangaError(kind=ErrorKind.LLM_RATE_LIMITED, message="rate limit")
    client = FakeLLMClient().with_failure(err)
    result = client.complete("p", model="m")
    assert isinstance(result, Failure)
    assert result.failure().kind == ErrorKind.LLM_RATE_LIMITED
    assert len(client.calls) == 1
