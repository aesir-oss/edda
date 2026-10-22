from typing import Any

import pytest

from edda.config import EddaConfig
from edda.jobs.runner import ToolFailure, ToolSpec, run_job
from edda.kernel import NotFoundError
from edda.llm import LLM, AssistantTurn, ChatResult, Tool, ToolCall, ToolTurn, Usage
from edda.llm.types import Stop


class _Script:
    """A provider that replays assistant turns and records what it was sent."""

    def __init__(self, *turns: tuple[AssistantTurn, Stop]) -> None:
        self.turns = list(turns)
        self.sent: list[Any] = []

    def chat(self, *, turns: Any, **_: Any) -> ChatResult:
        self.sent.append(list(turns))
        turn, stop = self.turns.pop(0)
        return ChatResult(
            turn=turn, stop=stop, usage=Usage(input_tokens=10, output_tokens=2), provider="local", model="m"
        )


def _call(name: str, call_id: str, **arguments: Any) -> AssistantTurn:
    return AssistantTurn(tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)])


ECHO = ToolSpec(
    tool=Tool(name="echo", description="echo", input_schema={"type": "object"}),
    run=lambda a: {"echo": a["text"]},
)


def _boom(args: dict[str, Any]) -> Any:
    if args.get("kind") == "missing":
        raise NotFoundError("no page gone.md")
    raise ToolFailure("not one of this job's proposals")


BOOM = ToolSpec(tool=Tool(name="boom", description="fails", input_schema={"type": "object"}), run=_boom)


def test_runs_tools_until_the_model_stops_and_returns_errors_to_it() -> None:
    script = _Script(
        (_call("echo", "1", text="hi"), "tool_use"),
        (
            AssistantTurn(
                tool_calls=[
                    ToolCall(id="2", name="boom", arguments={"kind": "missing"}),
                    ToolCall(id="3", name="nope", arguments={}),
                    ToolCall(id="4", name="echo", arguments={}),
                ]
            ),
            "tool_use",
        ),
        (AssistantTurn(text="Proposed two edits."), "end"),
    )
    llm = LLM(EddaConfig(), providers={"local": script})
    result = run_job(llm, "ingest", "ingest:1", system="s", prompt="go", tools=[ECHO, BOOM], proposals=lambda: ["p1"])
    assert (result.ending, result.turns, result.tool_calls, result.tool_errors) == ("end", 3, 4, 3)
    assert (result.final_text, result.proposals, result.model) == ("Proposed two edits.", ["p1"], "m")
    assert result.usage.input_tokens == 30
    results = script.sent[1][-1]
    assert isinstance(results, ToolTurn) and results.results[0].content == '{"echo": "hi"}'
    errors = script.sent[2][-1].results
    assert [r.is_error for r in errors] == [True, True, True]
    assert errors[0].content == "NotFoundError: no page gone.md"
    assert errors[1].content == "there is no tool named 'nope'"
    assert errors[2].content.startswith("KeyError")


@pytest.mark.parametrize(
    ("stop", "ending"), [("max_tokens", "max_tokens"), ("refusal", "refusal"), ("tool_use", "end")]
)
def test_reports_why_it_stopped(stop: Stop, ending: str) -> None:
    llm = LLM(EddaConfig(), providers={"local": _Script((AssistantTurn(text="x"), stop))})
    assert run_job(llm, "ingest", "j", system="s", prompt="p", tools=[ECHO]).ending == ending


def test_stops_at_the_turn_limit() -> None:
    script = _Script(*[(_call("echo", str(n), text="again"), "tool_use") for n in range(3)])
    llm = LLM(EddaConfig(), providers={"local": script})
    result = run_job(llm, "ingest", "j", system="s", prompt="p", tools=[ECHO], max_turns=3)
    assert (result.ending, result.turns, result.tool_calls, result.proposals) == ("turn_limit", 3, 3, [])


def test_tool_results_must_be_json() -> None:
    odd = ToolSpec(tool=Tool(name="odd", description="", input_schema={}), run=lambda a: object())
    script = _Script((_call("odd", "1"), "tool_use"), (AssistantTurn(text="done"), "end"))
    with pytest.raises(TypeError, match="cannot send object"):
        run_job(LLM(EddaConfig(), providers={"local": script}), "ingest", "j", system="s", prompt="p", tools=[odd])
