import json
from pathlib import Path
from typing import Any

import pytest

from edda.config import load_config
from edda.jobs import ingest
from edda.jobs.ingest import main
from edda.kernel import Kernel
from edda.llm import LLM, AssistantTurn, ChatResult, ToolCall, Usage, UserTurn
from edda.llm.types import Stop

pytestmark = pytest.mark.integration

NOTES = "887e1bdc"  # raw/887e1bdc...md, the reading notes on attention
ADDED = "Each head attends to the sequence with its own learned projections."


class _Recorded:
    """Replays the turns a tool-calling model gave for this source."""

    def __init__(self, turns: list[tuple[list[ToolCall], str, Stop]]) -> None:
        self.turns = turns
        self.prompts: list[str] = []
        self.systems: list[str] = []

    def chat(self, *, system: str, turns: Any, tools: Any, **_: Any) -> ChatResult:
        self.systems.append(system)
        if isinstance(turns[0], UserTurn):
            self.prompts.append(turns[0].text)
        calls, text, stop = self.turns.pop(0)
        return ChatResult(
            turn=AssistantTurn(text=text, tool_calls=calls),
            stop=stop,
            usage=Usage(input_tokens=1000, output_tokens=100),
            provider="local",
            model="recorded",
        )


def _edit_turns(old: str, new: str) -> list[tuple[list[ToolCall], str, Stop]]:
    return [
        ([ToolCall(id="c1", name="read_page", arguments={"page": "attention.md"})], "", "tool_use"),
        (
            [
                ToolCall(
                    id="c2",
                    name="propose_edit",
                    arguments={
                        "page": "attention.md",
                        "base_rev": 1,
                        "edits": [{"old": old, "new": new}],
                        "rationale": "The notes explain multi-head attention.",
                    },
                )
            ],
            "",
            "tool_use",
        ),
        (
            [
                ToolCall(
                    id="c3",
                    name="cite",
                    arguments={
                        "proposal": "prop_000001",
                        "claim": ADDED,
                        "locator": "Lines 5 to 6",
                    },
                )
            ],
            "",
            "tool_use",
        ),
        ([], "Proposed one cited edit to the attention page.", "end"),
    ]


def test_ingest_proposes_cited_edits_and_records_the_run(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    config = load_config(vault_copy)
    old = "their outputs are concatenated; the"
    model = _Recorded(_edit_turns(old, f"their outputs are concatenated. {ADDED} The"))
    result = ingest(kernel, LLM(config, providers={"local": model}), config, NOTES)

    assert (result.ending, result.proposals, result.tool_calls, result.tool_errors) == ("end", ["prop_000001"], 3, 0)
    assert result.usage.input_tokens == 4000
    proposal = kernel.proposal("prop_000001")
    assert proposal.job == result.job_id
    assert proposal.preview.issues == []
    assert f"+{ADDED}" not in proposal.preview.diff
    assert f"{ADDED}[^" in proposal.preview.diff

    prompt = model.prompts[0]
    assert "# Related pages" in prompt and "## /attention.md (revision 1)" in prompt
    assert "   4  - Scores are dot products" in prompt, "the source is shown with line numbers"
    assert "Page types this wiki accepts: Concept, Paper, Person, Project." in model.systems[0]
    record = json.loads((vault_copy / "jobs" / f"{result.job_id.replace(':', '_')}.json").read_text())
    assert record["proposals"] == ["prop_000001"]
    assert kernel.read_page("attention.md").rev == 1, "an ingest job writes no page"


def test_mistakes_go_back_to_the_model(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    config = load_config(vault_copy)
    turns: list[tuple[list[ToolCall], str, Stop]] = [
        (
            [
                ToolCall(
                    id="c1",
                    name="propose_edit",
                    arguments={
                        "page": "attention.md",
                        "base_rev": 7,
                        "edits": [{"old": "x", "new": "y"}],
                        "rationale": "r",
                    },
                )
            ],
            "",
            "tool_use",
        ),
        (
            [ToolCall(id="c2", name="cite", arguments={"proposal": "prop_000099", "claim": "c", "locator": "l"})],
            "",
            "tool_use",
        ),
        ([ToolCall(id="c3", name="read_source", arguments={"start": 3, "end": 3})], "", "tool_use"),
        (
            [ToolCall(id="c4", name="flag", arguments={"page": "bm25.md", "kind": "gap", "note": "no example"})],
            "",
            "tool_use",
        ),
        ([ToolCall(id="c5", name="search", arguments={"query": "attention"})], "", "tool_use"),
        (
            [
                ToolCall(
                    id="c6",
                    name="propose_page",
                    arguments={
                        "page": "heads.md",
                        "type": "Concept",
                        "title": "Heads",
                        "description": "d",
                        "body": "Heads split.\n",
                        "rationale": "r",
                    },
                )
            ],
            "",
            "tool_use",
        ),
        ([], "done", "end"),
    ]
    model = _Recorded(turns)
    result = ingest(kernel, LLM(config, providers={"local": model}), config, NOTES)
    assert (result.tool_calls, result.tool_errors) == (6, 2)
    assert len(result.proposals) == 2
    issues = kernel.proposal(result.proposals[1]).preview.issues
    assert issues == ['added text cites no source: "Heads split."']


def test_a_source_too_big_for_the_budget_is_read_in_parts(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    config = load_config(vault_copy)
    big = "".join(f"Line {n}: notes about ranking and retrieval.\n" for n in range(5000)).encode()
    added = kernel.add_source(big, extension="md", title="Long notes")
    model = _Recorded([([], "nothing to add", "end")])
    ingest(kernel, LLM(config, providers={"local": model}), config, added.source.hash)
    assert "The source has 5001 lines" in model.prompts[0]
    assert "read it in parts with read_source" in model.prompts[0]


def test_main_reports_usage_and_unreachable_models(vault_copy: Path) -> None:
    assert main([]) == 2
    (vault_copy / "edda.toml").write_text('[llm.endpoints.local]\nbase_url = "http://127.0.0.1:9/v1"\nmodel = "m"\n')
    assert main([str(vault_copy), NOTES]) == 1


def test_small_models_get_pointed_at_their_own_proposals(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    config = load_config(vault_copy)
    page = {
        "page": "heads.md",
        "type": "Concept",
        "title": "Heads",
        "description": "d",
        "body": "Heads split.\n",
        "rationale": "r",
    }
    turns: list[tuple[list[ToolCall], str, Stop]] = [
        (
            [ToolCall(id="c1", name="cite", arguments={"proposal": "edit:/x.md/1", "claim": "c", "locator": "l"})],
            "",
            "tool_use",
        ),
        ([ToolCall(id="c2", name="propose_page", arguments=page)], "", "tool_use"),
        ([ToolCall(id="c3", name="propose_page", arguments={**page, "page": "/heads.md"})], "", "tool_use"),
        (
            [ToolCall(id="c4", name="cite", arguments={"proposal": "made-up", "claim": "c", "locator": "l"})],
            "",
            "tool_use",
        ),
        ([], "done", "end"),
    ]
    model = _Recorded(turns)
    sent: list[Any] = []
    original = model.chat

    def spy(**kwargs: Any) -> ChatResult:
        sent.append(kwargs["turns"])
        return original(**kwargs)

    model.chat = spy  # type: ignore[method-assign]
    result = ingest(kernel, LLM(config, providers={"local": model}), config, NOTES)
    assert result.proposals == ["prop_000001"], "the second proposal for heads.md was refused"
    errors = [r.content for t in sent[-1] if t.role == "tool" for r in t.results]
    assert "yours: none yet" in errors[0]
    assert "you already proposed heads.md as prop_000001" in errors[2]
    assert "yours: prop_000001" in errors[3]
