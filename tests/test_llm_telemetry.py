from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from tendermate_shared.agents import telemetry
from tendermate_shared.agents.telemetry import LlmTelemetryCallback

ATTRS = {"agent": "verify_match", "provider": "deepseek", "model": "deepseek-chat"}


@pytest.fixture
def metrics(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    mock = MagicMock()
    monkeypatch.setattr(telemetry, "metrics", mock)
    return mock


def _callback() -> LlmTelemetryCallback:
    return LlmTelemetryCallback("verify_match", "deepseek", "deepseek-chat")


def test_success_counts_call_duration_and_tokens(metrics: MagicMock) -> None:
    cb, run_id = _callback(), uuid.uuid4()
    message = AIMessage(
        "ok", usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}
    )

    cb.on_chat_model_start({}, [[]], run_id=run_id)
    cb.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id)

    metrics.count.assert_called_once_with("llm.call", 1, attributes={**ATTRS, "result": "ok"})
    duration = metrics.distribution.call_args_list[0]
    assert duration.args[0] == "llm.call.duration" and duration.kwargs["unit"] == "second"
    metrics.distribution.assert_any_call("llm.tokens.prompt", 120, attributes=ATTRS)
    metrics.distribution.assert_any_call("llm.tokens.completion", 30, attributes=ATTRS)


def test_token_usage_from_llm_output(metrics: MagicMock) -> None:
    cb, run_id = _callback(), uuid.uuid4()
    result = LLMResult(
        generations=[], llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}
    )

    cb.on_llm_start({}, ["p"], run_id=run_id)
    cb.on_llm_end(result, run_id=run_id)

    metrics.distribution.assert_any_call("llm.tokens.prompt", 7, attributes=ATTRS)


def test_error_counts_with_type(metrics: MagicMock) -> None:
    cb, run_id = _callback(), uuid.uuid4()

    cb.on_chat_model_start({}, [[]], run_id=run_id)
    cb.on_llm_error(TimeoutError("slow"), run_id=run_id)

    metrics.count.assert_called_once_with(
        "llm.call", 1, attributes={**ATTRS, "result": "error", "error_type": "TimeoutError"}
    )
