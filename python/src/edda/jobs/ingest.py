"""The ingest job: reads one raw source and proposes the wiki edits it supports, each claim cited to the source.

    pixi run ingest examples/vault 887e1bdc

The job writes nothing itself. Its proposals wait in the review queue, and a record of the run (model, tokens, tool
calls, proposals) is saved in ``<vault>/jobs/<job id>.json``.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from edda.config import EddaConfig, load_config
from edda.context import ContextOverflow, pack
from edda.jobs.runner import JobResult, ToolFailure, ToolSpec, run_job
from edda.kernel import Kernel
from edda.llm import LLM, LLMError, Tool
from edda.models import TextEdit

log = structlog.get_logger("edda.jobs.ingest")


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_STR = {"type": "string"}
_INT = {"type": "integer"}

INSTRUCTIONS = """You maintain a wiki of linked, cited Markdown pages. A new source has arrived. Work out what it adds
to the wiki and propose those changes with the tools. You cannot write pages directly: every change you make is a
proposal that a person reviews.

Rules the kernel enforces, so follow them or your proposal will be refused:
- Every paragraph you add must be backed by the source: after proposing, call cite with a short exact quote of the
  text you added (the claim) and where in the source it comes from (the locator, such as "lines 3-5" or
  "section 2"). Uncited additions cannot be accepted.
- Edit existing pages with propose_edit. Each edit replaces an exact piece of the page's current text (old) with new
  text (new); old must appear exactly once in the page. base_rev is the page's revision, shown in its header.
- Create a page with propose_page only when no existing page fits. Its id is a path such as "concepts/topic.md".
- Link to other pages with root-relative links, such as [Transformer](/transformer.md), and only to pages that exist.
- Use flag for contradictions between the source and a page, or for gaps you cannot fill from the source.
- Prefer a few precise changes over many vague ones, and do not restate what a page already says.

When you have proposed and cited everything the source supports, stop calling tools and reply with one short sentence
summarizing what you proposed."""


def ingest_tools(kernel: Kernel, job_id: str, source_hash: str, made: list[str]) -> list[ToolSpec]:
    """The system calls the ingest job may use, bound to the source being ingested."""

    def remember(proposal_id: str) -> None:
        if proposal_id not in made:
            made.append(proposal_id)

    def proposal_view(proposal_id: str) -> dict[str, Any]:
        proposal = kernel.proposal(proposal_id)
        return {"proposal": proposal.id, "issues": proposal.preview.issues, "diff": proposal.preview.diff}

    def propose_edit(args: dict[str, Any]) -> dict[str, Any]:
        edits = [TextEdit.model_validate(e) for e in args["edits"]]
        proposal = kernel.propose_edit(job_id, args["page"], int(args["base_rev"]), edits, args["rationale"])
        remember(proposal.id)
        return proposal_view(proposal.id)

    def propose_page(args: dict[str, Any]) -> dict[str, Any]:
        page = args["page"].lstrip("/")
        for proposal_id in made:
            if kernel.proposal(proposal_id).change.page == page:
                raise ToolFailure(f"you already proposed {page} as {proposal_id}; cite or edit that proposal instead")
        proposal = kernel.propose_page(
            job_id,
            args["page"],
            type=args["type"],
            title=args["title"],
            description=args["description"],
            tags=list(args.get("tags") or []),
            body=args["body"],
            rationale=args["rationale"],
        )
        remember(proposal.id)
        return proposal_view(proposal.id)

    def cite(args: dict[str, Any]) -> dict[str, Any]:
        proposal_id = args["proposal"]
        if proposal_id not in made:
            yours = ", ".join(made) or "none yet"
            raise ToolFailure(
                f"{proposal_id!r} is not one of this job's proposals; use the id a propose_* call returned (yours: {yours})"
            )
        kernel.cite(proposal_id, args["claim"], source_hash, args["locator"])
        return proposal_view(proposal_id)

    def flag(args: dict[str, Any]) -> dict[str, Any]:
        proposal = kernel.flag(job_id, args["page"], args["kind"], args["note"])
        remember(proposal.id)
        return {"proposal": proposal.id}

    def read_source(args: dict[str, Any]) -> str:
        return kernel.read_source(source_hash, args.get("start"), args.get("end")).text

    return [
        ToolSpec(
            tool=Tool(
                name="search",
                description="Search the wiki's pages by words; returns page ids, titles and snippets.",
                input_schema=_schema({"query": _STR}, ["query"]),
            ),
            run=lambda a: [
                {"id": h.id, "title": h.title, "description": h.description} for h in kernel.search(a["query"], 8)
            ],
        ),
        ToolSpec(
            tool=Tool(
                name="read_page",
                description="Read a page's current Markdown body, revision and sources.",
                input_schema=_schema({"page": _STR}, ["page"]),
            ),
            run=lambda a: (lambda p: {"id": p.id, "rev": p.rev or 0, "title": p.title, "body": p.body})(
                kernel.read_page(a["page"])
            ),
        ),
        ToolSpec(
            tool=Tool(
                name="read_source",
                description="Read the new source, or lines start to end of it (1-based, inclusive).",
                input_schema=_schema({"start": _INT, "end": _INT}, []),
            ),
            run=read_source,
        ),
        ToolSpec(
            tool=Tool(
                name="propose_edit",
                description="Propose text edits to an existing page. Returns the proposal id, a diff and any issues.",
                input_schema=_schema(
                    {
                        "page": _STR,
                        "base_rev": _INT,
                        "edits": {
                            "type": "array",
                            "items": _schema({"old": _STR, "new": _STR}, ["old", "new"]),
                        },
                        "rationale": _STR,
                    },
                    ["page", "base_rev", "edits", "rationale"],
                ),
            ),
            run=propose_edit,
        ),
        ToolSpec(
            tool=Tool(
                name="propose_page",
                description="Propose a new page. Returns the proposal id, a diff and any issues.",
                input_schema=_schema(
                    {
                        "page": _STR,
                        "type": _STR,
                        "title": _STR,
                        "description": _STR,
                        "tags": {"type": "array", "items": _STR},
                        "body": _STR,
                        "rationale": _STR,
                    },
                    ["page", "type", "title", "description", "body", "rationale"],
                ),
            ),
            run=propose_page,
        ),
        ToolSpec(
            tool=Tool(
                name="cite",
                description=(
                    "Cite the new source for a claim in one of your proposals: claim is exact text the proposal adds, "
                    "locator is where in the source it comes from. Returns the remaining issues."
                ),
                input_schema=_schema(
                    {"proposal": _STR, "claim": _STR, "locator": _STR}, ["proposal", "claim", "locator"]
                ),
            ),
            run=cite,
        ),
        ToolSpec(
            tool=Tool(
                name="flag",
                description="Report a contradiction, outdated claim or gap on a page for a person to look at.",
                input_schema=_schema({"page": _STR, "kind": _STR, "note": _STR}, ["page", "kind", "note"]),
            ),
            run=flag,
        ),
    ]


def ingest(kernel: Kernel, llm: LLM, config: EddaConfig, source: str, *, max_turns: int = 16) -> JobResult:
    """Ingests one raw source (full hash or unique prefix) and saves the job record.

    Raises:
        LLMError: The model could not be reached or refused to work.
        NotFoundError: No raw source by that name.
    """
    text = kernel.read_source(source)
    job_id = f"ingest:{text.hash[:8]}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    types = ", ".join(config.vault.get("types") or ["Concept", "Paper", "Person", "Project"])
    instructions = f"{INSTRUCTIONS}\n\nPage types this wiki accepts: {types}."
    header = f"Source {text.hash[:12]}: {text.title or 'untitled'}" + (f" ({text.url})" if text.url else "")
    body = f"{header}\n\n{_numbered(text.text)}"
    try:
        context = pack(kernel, instructions=instructions, source=body, budget=config.budgets.ingest)
    except ContextOverflow:
        lines = text.text.count("\n") + 1
        stub = f"{header}\n\nThe source has {lines} lines, too many to show at once; read it in parts with read_source."
        context = pack(kernel, instructions=instructions, source=stub, budget=config.budgets.ingest)
    made: list[str] = []
    result = run_job(
        llm,
        "ingest",
        job_id,
        system=context.system,
        prompt=context.user_text(),
        tools=ingest_tools(kernel, job_id, text.hash, made),
        max_turns=max_turns,
        proposals=lambda: list(made),
    )
    record = kernel.vault / "jobs" / f"{job_id.replace(':', '_')}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return result


def _numbered(text: str) -> str:
    """The source with line numbers, so the model can give locators such as "lines 3-5"."""
    return "\n".join(f"{n:>4}  {line}" for n, line in enumerate(text.splitlines(), start=1))


def main(argv: list[str] | None = None) -> int:
    """``ingest <vault> <source>``: run the ingest job on one raw source."""
    from edda.log import configure_logging

    configure_logging()
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        log.error("usage", expected="ingest <vault> <source hash or prefix>")
        return 2
    vault = Path(args[0])
    config = load_config(vault)
    try:
        result = ingest(Kernel(vault), LLM(config), config, args[1])
    except LLMError as exc:
        log.error("ingest_failed", error=str(exc))
        return 1
    log.info("ingest_result", **result.model_dump(exclude={"final_text"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
