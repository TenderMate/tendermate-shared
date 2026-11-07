"""BaseAgent.run/arun are one LangChain chain run named after the agent, parenting the LLM run."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tracers.context import collect_runs
from tendermate_shared.agents.base import BaseAgent
from tendermate_shared.agents.config import GenerationConfig
from tendermate_shared.agents.input import AgentInput


class _In(AgentInput):
    text: str

    def render_human(self) -> str:
        return self.text


class _EchoAgent(BaseAgent[_In, str]):
    def _system_prompt(self) -> str:
        return "sys"

    def _parse_response(self, response_text: str) -> str:
        return response_text


class _FailingParseAgent(_EchoAgent):
    def _parse_response(self, response_text: str) -> str:
        raise ValueError("bad payload")


def test_run_is_one_chain_run_named_after_agent_with_llm_child() -> None:
    with collect_runs() as cb:
        res = _EchoAgent(FakeListChatModel(responses=["ok"])).run(_In(text="x"))

    assert res.data == "ok"
    (root,) = cb.traced_runs
    assert root.name == "_EchoAgent"
    assert root.run_type == "chain"
    assert [c.run_type for c in root.child_runs] == ["llm"]


async def test_arun_is_one_chain_run_named_after_agent_with_llm_child() -> None:
    with collect_runs() as cb:
        res = await _EchoAgent(FakeListChatModel(responses=["ok"])).arun(_In(text="x"))

    assert res.data == "ok"
    (root,) = cb.traced_runs
    assert root.name == "_EchoAgent"
    assert root.run_type == "chain"
    assert [c.run_type for c in root.child_runs] == ["llm"]


def test_gen_config_kwargs_reach_model() -> None:
    model = MagicMock()
    model.invoke.return_value = AIMessage(content="ok")

    res = _EchoAgent(model).run(_In(text="x"), gen_config=GenerationConfig(temperature=0.3))

    assert res.data == "ok"
    assert model.invoke.call_args.kwargs == {"temperature": 0.3}


async def test_arun_gen_config_kwargs_reach_model() -> None:
    model = MagicMock()

    async def _ainvoke(messages: object, **kwargs: object) -> AIMessage:
        return AIMessage(content="ok")

    model.ainvoke = MagicMock(side_effect=_ainvoke)

    await _EchoAgent(model).arun(_In(text="x"), gen_config=GenerationConfig(top_p=0.5))

    assert model.ainvoke.call_args.kwargs == {"top_p": 0.5}


def test_run_without_tracer_is_plain() -> None:
    res = _EchoAgent(FakeListChatModel(responses=["ok"])).run(_In(text="x"))

    assert res.data == "ok"
    assert res.model == "FakeListChatModel"


def test_parse_error_propagates() -> None:
    with pytest.raises(ValueError, match="bad payload"):
        _FailingParseAgent(FakeListChatModel(responses=["ok"])).run(_In(text="x"))


async def test_arun_parse_error_propagates() -> None:
    with pytest.raises(ValueError, match="bad payload"):
        await _FailingParseAgent(FakeListChatModel(responses=["ok"])).arun(_In(text="x"))
