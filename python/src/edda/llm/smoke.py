"""``llm-smoke``: one tool-calling round trip against a vault's configured model, to check it works before a job.

pixi run llm-smoke examples/vault
"""

import sys
import time
from pathlib import Path

import structlog
from pydantic import ValidationError

from edda.config import EddaConfig, load_config
from edda.llm.client import LLM
from edda.llm.errors import LLMError
from edda.llm.types import Job, Tool, ToolResult, ToolTurn, UserTurn

log = structlog.get_logger("edda.llm.smoke")

SEARCH = Tool(
    name="search",
    description="Search the wiki's pages and return the best matches.",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "Words to search for"}},
        "required": ["query"],
        "additionalProperties": False,
    },
)


def smoke(llm: LLM, job: Job = "ingest") -> bool:
    """Asks the model to call ``search``, returns a result, and checks it answers. True when both steps work."""
    system = "You maintain a wiki. Use the tools you are given; never guess what a page says."
    turns: list[UserTurn | ToolTurn] = [UserTurn(text="Which page explains BM25? Use the search tool.")]
    started = time.perf_counter()
    first = llm.chat(job, "smoke:1", system=system, turns=turns, tools=[SEARCH])
    calls = [c for c in first.turn.tool_calls if c.name == "search" and isinstance(c.arguments.get("query"), str)]
    if not calls:
        log.error("smoke_no_tool_call", stop=first.stop, text=first.turn.text[:200])
        return False
    results = ToolTurn(results=[ToolResult(call_id=calls[0].id, content='[{"id": "bm25.md", "title": "BM25"}]')])
    second = llm.chat(job, "smoke:1", system=system, turns=[*turns, first.turn, results], tools=[SEARCH])
    ok = "bm25" in second.turn.text.lower()
    log.info(
        "smoke_done",
        ok=ok,
        model=second.model,
        seconds=round(time.perf_counter() - started, 1),
        **llm.usage("smoke:1").model_dump(),
    )
    return ok


def main(argv: list[str] | None = None) -> int:
    """``llm-smoke <vault> [provider]``: exit 0 when the model calls tools and answers.

    The provider defaults to the ingest job's; naming one (an endpoint, or ``anthropic``) tests it instead.
    """
    from edda.log import configure_logging

    configure_logging()
    args = sys.argv[1:] if argv is None else argv
    vault = Path(args[0]) if args else Path("examples/vault")
    config = load_config(vault)
    if len(args) > 1:
        data = config.model_dump()
        data["llm"]["ingest"] = args[1]
        try:
            config = EddaConfig.model_validate(data)
        except ValidationError as exc:
            log.error("smoke_unknown_provider", provider=args[1], error=str(exc.errors()[0]["msg"]))
            return 2
    try:
        return 0 if smoke(LLM(config)) else 1
    except LLMError as exc:
        log.error("smoke_failed", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
