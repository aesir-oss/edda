"""The job runner: a loop of model calls whose tools are the kernel's system calls.

Everything a job does to the vault arrives as a proposal, because the only write tools it is given are the
``propose_*``, ``cite`` and ``flag`` system calls. A tool that fails returns its error to the model instead of ending
the job, so the model can correct a bad edit or an uncited paragraph.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from edda.kernel import KernelError
from edda.llm import LLM, Job, Tool, ToolResult, ToolTurn, Turn, Usage, UserTurn

log = structlog.get_logger("edda.jobs")

Handler = Callable[[dict[str, Any]], Any]
Ending = Literal["end", "max_tokens", "refusal", "turn_limit"]


class ToolSpec(BaseModel):
    """A tool the model may call, and the function that runs it. A result that is not a string is sent as JSON."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    tool: Tool
    run: Handler


class JobResult(BaseModel):
    """What a job did: the proposals it made, why it stopped, and the tokens it used."""

    job_id: str
    job: Job
    provider: str
    model: str
    ending: Ending
    turns: int
    tool_calls: int
    tool_errors: int
    proposals: list[str] = Field(default_factory=list)
    final_text: str = ""
    usage: Usage = Field(default_factory=Usage)


class ToolFailure(ValueError):
    """A tool was called with arguments it cannot use; the message goes back to the model."""


def run_job(
    llm: LLM,
    job: Job,
    job_id: str,
    *,
    system: str,
    prompt: str,
    tools: Sequence[ToolSpec],
    max_turns: int = 16,
    proposals: Callable[[], list[str]] | None = None,
) -> JobResult:
    """Runs the model with ``tools`` until it stops calling them, or ``max_turns`` model calls have been made.

    ``proposals`` reports the ids of the proposals made so far, for the result.
    """
    by_name = {spec.tool.name: spec for spec in tools}
    turns: list[Turn] = [UserTurn(text=prompt)]
    calls = errors = 0
    ending: Ending = "turn_limit"
    provider, model = llm.provider_for(job)
    final_text = ""
    for _ in range(max_turns):
        result = llm.chat(job, job_id, system=system, turns=turns, tools=[s.tool for s in tools])
        provider, model = result.provider, result.model
        turns.append(result.turn)
        final_text = result.turn.text
        if result.stop != "tool_use" or not result.turn.tool_calls:
            ending = "max_tokens" if result.stop == "max_tokens" else "refusal" if result.stop == "refusal" else "end"
            break
        outputs = []
        for call in result.turn.tool_calls:
            calls += 1
            content, failed = _run(by_name.get(call.name), call.name, call.arguments)
            errors += failed
            outputs.append(ToolResult(call_id=call.id, content=content, is_error=failed))
        turns.append(ToolTurn(results=outputs))
    made = proposals() if proposals else []
    log.info("job_done", job=job, job_id=job_id, ending=ending, tool_calls=calls, tool_errors=errors, proposals=made)
    return JobResult(
        job_id=job_id,
        job=job,
        provider=provider,
        model=model,
        ending=ending,
        turns=sum(1 for t in turns if t.role == "assistant"),
        tool_calls=calls,
        tool_errors=errors,
        proposals=made,
        final_text=final_text,
        usage=llm.usage(job_id),
    )


def _run(spec: ToolSpec | None, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
    if spec is None:
        return f"there is no tool named {name!r}", True
    try:
        output = spec.run(arguments)
    except (KernelError, ToolFailure, ValidationError, KeyError, TypeError) as exc:
        log.info("tool_error", tool=name, error=str(exc))
        return f"{type(exc).__name__}: {exc}", True
    return (output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=_json)), False


def _json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    raise TypeError(f"cannot send {type(value).__name__} to the model")
