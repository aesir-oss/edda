"""The FastAPI app over one vault: pages, search, sources and links, proposals and their review, jobs and events."""

import os
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel

from edda import okf_version, review
from edda.api.events import Events, stream
from edda.config import ConfigError, load_config
from edda.drivers import DriverError, FetchFailed, NotAllowed, import_markdown, import_url
from edda.jobs.background import Jobs, Progress
from edda.kernel import ConflictError, InvalidRequestError, Kernel, NotFoundError
from edda.log import configure_logging
from edda.models import (
    AcceptRequest,
    AddedSource,
    Decision,
    GraphIssues,
    IngestRequest,
    JobOutcome,
    JobRun,
    JournalEntry,
    Links,
    Page,
    PageSummary,
    Proposal,
    RejectRequest,
    SearchHit,
    Source,
    SourceSummary,
    WebImportRequest,
)
from edda.policy import PolicyError, load_policy

log = structlog.get_logger("edda.api")

VAULT_ENV = "EDDA_VAULT"
MAX_UPLOAD_BYTES = 10_000_000

Ingest = Callable[[Kernel, str, Progress], JobOutcome]


class VaultInfo(BaseModel):
    """What the shell shows about the open vault."""

    title: str
    okf_version: str
    pages: int


class Health(BaseModel):
    """Liveness."""

    status: str


def get_kernel(request: Request) -> Kernel:
    """The app's kernel, opened on first use so the app can start (and export its schema) without a vault."""
    kernel: Kernel | None = request.app.state.kernel
    if kernel is None:
        vault: Path | None = request.app.state.vault
        if vault is None:
            raise HTTPException(503, f"no vault configured; set {VAULT_ENV}")
        kernel = Kernel(vault)
        kernel.listen(request.app.state.events.publish)
        request.app.state.kernel = kernel
        log.info("vault_opened", vault=str(vault), title=kernel.title)
    return kernel


KernelDep = Annotated[Kernel, Depends(get_kernel)]


def get_today() -> date:
    """Today's date in UTC, which ``stale_after`` is compared with; tests override it."""
    return datetime.now(UTC).date()


TodayDep = Annotated[date, Depends(get_today)]


def is_stale(stale_after: str | None, today: date) -> bool:
    """True when a page's ``stale_after`` date (or the date part of a timestamp) has passed."""
    if not stale_after:
        return False
    try:
        return date.fromisoformat(stale_after[:10]) < today
    except ValueError:
        log.warning("unreadable_stale_after", stale_after=stale_after)
        return False


_NOTES = {
    "turn_limit": "stopped at its limit of {turns} model turns; some of its proposals may be unfinished",
    "max_tokens": "the model's last reply was cut off at max_output_tokens",
    "refusal": "the model refused to continue",
}


def run_ingest(kernel: Kernel, source: str, progress: Progress) -> JobOutcome:
    """Runs the ingest job with the vault's own model settings, reporting each turn; returns what it made."""
    from edda.jobs.ingest import ingest
    from edda.llm import LLM

    config = load_config(kernel.vault)
    result = ingest(kernel, LLM(config), config, source, on_turn=progress)
    return JobOutcome(proposals=result.proposals, note=_NOTES.get(result.ending, "").format(turns=result.turns) or None)


def create_app(
    vault: Path | None = None,
    kernel: Kernel | None = None,
    ingest: Ingest = run_ingest,
    web_client: httpx.Client | None = None,
) -> FastAPI:
    """Builds the API for ``vault`` (default: the ``EDDA_VAULT`` environment variable), or around ``kernel``.

    ``ingest`` runs the ingest job and ``web_client`` fetches web pages; tests pass ones that need no model or network.
    """
    configure_logging()
    if vault is None and kernel is None and os.environ.get(VAULT_ENV):
        vault = Path(os.environ[VAULT_ENV])
    events = Events()
    jobs = Jobs(listener=lambda run: events.publish("job", run.model_dump(mode="json")))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        events.close()
        jobs.shutdown()

    app = FastAPI(
        title="Edda",
        version="0.1.0",
        summary="An Edda vault: pages, search and sources, proposals and their review, and the jobs that make them.",
        lifespan=lifespan,
    )
    app.state.vault = vault
    app.state.kernel = kernel
    app.state.events = events
    app.state.jobs = jobs
    if kernel is not None:
        kernel.listen(events.publish)

    @app.middleware("http")
    async def log_requests(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(DriverError)
    async def undriveable(_: Request, exc: DriverError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ConfigError)
    @app.exception_handler(PolicyError)
    async def misconfigured(_: Request, exc: ValueError) -> JSONResponse:
        log.error("vault_misconfigured", error=str(exc))
        return JSONResponse({"detail": str(exc)}, status_code=500)

    @app.exception_handler(NotAllowed)
    async def not_allowed(_: Request, exc: NotAllowed) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(FetchFailed)
    async def fetch_failed(_: Request, exc: FetchFailed) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(InvalidRequestError)
    async def invalid(_: Request, exc: InvalidRequestError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/health", tags=["meta"])
    def health() -> Health:
        return Health(status="ok")

    @app.get("/api/vault", tags=["meta"])
    def vault_info(kernel: KernelDep) -> VaultInfo:
        return VaultInfo(title=kernel.title, okf_version=okf_version(), pages=len(kernel.pages()))

    @app.get("/api/pages", tags=["pages"])
    def list_pages(
        kernel: KernelDep,
        today: TodayDep,
        tag: Annotated[str | None, Query(description="Only pages with this tag")] = None,
        type: Annotated[str | None, Query(description="Only pages of this type")] = None,
        source: Annotated[
            str | None, Query(description="Only pages citing this raw source (full hash or a prefix)")
        ] = None,
        status: Annotated[str | None, Query(description="Only pages with this status, such as draft")] = None,
        stale: Annotated[bool | None, Query(description="Only pages past (or not past) stale_after")] = None,
    ) -> list[PageSummary]:
        prefix = source.lower() if source else None
        listed = []
        for page in kernel.pages():
            page = page.model_copy(update={"stale": is_stale(page.stale_after, today)})
            if (
                (tag is None or tag in page.tags)
                and (type is None or page.type == type)
                and (prefix is None or any(h.startswith(prefix) for h in page.sources))
                and (status is None or page.status == status)
                and (stale is None or page.stale == stale)
            ):
                listed.append(page)
        return listed

    @app.get("/api/sources", tags=["sources"])
    def list_sources(kernel: KernelDep) -> list[SourceSummary]:
        return kernel.sources()

    @app.post(
        "/api/sources/markdown",
        tags=["sources"],
        status_code=201,
        responses={200: {"model": AddedSource, "description": "The same bytes were already stored"}},
        openapi_extra={"requestBody": {"required": True, "content": {"text/markdown": {"schema": {"type": "string"}}}}},
    )
    async def import_markdown_source(
        request: Request,
        response: Response,
        kernel: KernelDep,
        filename: Annotated[str | None, Query(description="The file's name, the title of last resort")] = None,
        url: Annotated[str | None, Query(description="Where the file came from, if anywhere")] = None,
        title: Annotated[str | None, Query(description="Overrides the title read from the file")] = None,
    ) -> AddedSource:
        """Stores the request body, a Markdown file, unedited as a raw source."""
        data = await request.body()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"sources are limited to {MAX_UPLOAD_BYTES} bytes")
        added = import_markdown(kernel, data, filename=filename, url=url, title=title)
        if not added.added:
            response.status_code = 200
        return added

    @app.post(
        "/api/sources/web",
        tags=["sources"],
        status_code=201,
        responses={
            200: {"model": AddedSource, "description": "The same text was already stored"},
            403: {"description": "policy.toml does not allow fetching from that host"},
            502: {"description": "The page could not be fetched"},
        },
    )
    def import_web_source(body: WebImportRequest, response: Response, kernel: KernelDep) -> AddedSource:
        """Fetches a page from a host ``[network] allow`` lists and stores its readable text as Markdown."""
        config = load_config(kernel.vault).drivers.web
        network = load_policy(kernel.vault).network
        added = import_url(kernel, body.url, network=network, config=config, client=web_client, title=body.title)
        if not added.added:
            response.status_code = 200
        return added

    @app.get("/api/pages/{page_id:path}", tags=["pages"])
    def read_page(page_id: str, kernel: KernelDep) -> Page:
        return kernel.read_page(page_id)

    @app.get("/api/links/{page_id:path}", tags=["pages"])
    def links(page_id: str, kernel: KernelDep) -> Links:
        return kernel.links(page_id)

    @app.get("/api/history/{page_id:path}", tags=["pages"])
    def history(page_id: str, kernel: KernelDep) -> list[JournalEntry]:
        return kernel.history(page_id)

    @app.get("/api/search", tags=["search"])
    def search(
        kernel: KernelDep,
        q: Annotated[str, Query(min_length=1, description="Words to search for")],
        k: Annotated[int, Query(ge=1, le=100, description="How many results")] = 10,
    ) -> list[SearchHit]:
        return kernel.search(q, k)

    @app.get("/api/sources/{source_hash}", tags=["sources"])
    def read_source(
        source_hash: str,
        kernel: KernelDep,
        start: Annotated[int | None, Query(ge=1, description="First line, 1-based")] = None,
        end: Annotated[int | None, Query(ge=1, description="Last line, inclusive")] = None,
    ) -> Source:
        return kernel.read_source(source_hash, start, end)

    @app.get("/api/graph/issues", tags=["graph"])
    def graph_issues(kernel: KernelDep) -> GraphIssues:
        return kernel.graph_issues()

    @app.post("/api/index/refresh", tags=["meta"], status_code=204)
    def refresh_index(kernel: KernelDep) -> Response:
        kernel.refresh_index()
        return Response(status_code=204)

    @app.get("/api/proposals", tags=["proposals"])
    def list_proposals(
        kernel: KernelDep,
        status: Annotated[str | None, Query(description="Only proposals in this state, such as pending")] = None,
    ) -> list[Proposal]:
        return [p for p in kernel.proposals() if status is None or p.status == status]

    @app.get("/api/proposals/{proposal_id}", tags=["proposals"])
    def read_proposal(proposal_id: str, kernel: KernelDep) -> Proposal:
        return kernel.proposal(proposal_id)

    @app.post(
        "/api/proposals/{proposal_id}/accept",
        tags=["proposals"],
        responses={409: {"description": "The page changed since the proposal was made"}},
    )
    def accept_proposal(proposal_id: str, kernel: KernelDep, body: AcceptRequest | None = None) -> Decision:
        """Applies a proposal, or the owner's revision of it (``edits`` for an edit, ``page`` for a new page)."""
        revision = body or AcceptRequest()
        return review.accept(kernel, proposal_id, edits=revision.edits, page=revision.page)

    @app.post("/api/proposals/{proposal_id}/reject", tags=["proposals"])
    def reject_proposal(proposal_id: str, body: RejectRequest, kernel: KernelDep) -> Decision:
        return review.reject(kernel, proposal_id, body.reason)

    @app.post("/api/jobs/ingest", tags=["jobs"], status_code=202)
    def start_ingest(body: IngestRequest, kernel: KernelDep) -> JobRun:
        """Queues the ingest job for a raw source; its proposals arrive as events while it runs."""
        source = kernel.read_source(body.source).hash
        return jobs.start("ingest", source, lambda progress: ingest(kernel, source, progress))

    @app.get("/api/jobs", tags=["jobs"])
    def list_jobs() -> list[JobRun]:
        return jobs.runs()

    @app.get("/api/jobs/{run_id}", tags=["jobs"])
    def read_job(run_id: str) -> JobRun:
        run = jobs.run(run_id)
        if run is None:
            raise HTTPException(404, f"no job {run_id}")
        return run

    @app.get("/api/events", tags=["events"], response_class=EventSourceResponse)
    async def event_stream() -> AsyncIterable[ServerSentEvent]:
        """``proposal``, ``source``, ``page`` and ``job`` events as the vault changes, after a ``ready`` event."""
        async for event in stream(events):
            yield event

    return app
