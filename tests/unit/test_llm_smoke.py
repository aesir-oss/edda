from pathlib import Path
from typing import Any

import pytest

from edda.config import EddaConfig
from edda.llm import LLM, AssistantTurn, ChatResult, LLMError, ToolCall, Usage
from edda.llm import smoke as smoke_module
from edda.llm.smoke import main, smoke


class _Scripted:
    def __init__(self, turns: list[AssistantTurn]) -> None:
        self.turns = turns

    def chat(self, **_: Any) -> ChatResult:
        turn = self.turns.pop(0)
        return ChatResult(
            turn=turn,
            stop="tool_use" if turn.tool_calls else "end",
            usage=Usage(input_tokens=5),
            provider="local",
            model="qwen",
        )


def _llm(*turns: AssistantTurn) -> LLM:
    return LLM(EddaConfig(), providers={"local": _Scripted(list(turns))})


def test_passes_when_the_model_calls_the_tool_and_uses_the_result() -> None:
    call = AssistantTurn(tool_calls=[ToolCall(id="1", name="search", arguments={"query": "BM25"})])
    assert smoke(_llm(call, AssistantTurn(text="See the BM25 page.")))


def test_fails_without_a_tool_call_or_a_grounded_answer() -> None:
    assert not smoke(_llm(AssistantTurn(text="BM25 is a ranking function.")))
    call = AssistantTurn(tool_calls=[ToolCall(id="1", name="search", arguments={})])
    assert not smoke(_llm(call))
    good = AssistantTurn(tool_calls=[ToolCall(id="1", name="search", arguments={"query": "x"})])
    assert not smoke(_llm(good, AssistantTurn(text="I don't know.")))


def test_main_reports_outcomes(monkeypatch: pytest.MonkeyPatch, example_vault: Path) -> None:
    monkeypatch.setattr(smoke_module, "smoke", lambda llm: True)
    assert main([str(example_vault)]) == 0
    monkeypatch.setattr(smoke_module, "smoke", lambda llm: False)
    assert main([]) == 1

    def unreachable(llm: LLM) -> bool:
        raise LLMError("is the model server running?")

    monkeypatch.setattr(smoke_module, "smoke", unreachable)
    assert main([]) == 1


def test_main_can_test_another_provider(monkeypatch: pytest.MonkeyPatch, example_vault: Path) -> None:
    chosen: list[str] = []

    def record(llm: LLM) -> bool:
        chosen.append(llm.provider_for("ingest")[0])
        return True

    monkeypatch.setattr(smoke_module, "smoke", record)
    assert main([str(example_vault), "nvidia"]) == 0
    assert chosen == ["nvidia"]
    assert main([str(example_vault), "no-such-endpoint"]) == 2
