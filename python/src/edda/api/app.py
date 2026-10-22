"""The read-only FastAPI app over one vault: pages, search, sources, links and the link graph."""

import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from edda import okf_version
from edda.kernel import InvalidRequestError, Kernel, NotFoundError
from edda.log import configure_logging
from edda.models import GraphIssues, JournalEntry, Links, Page, PageSummary, SearchHit, Source

log = structlog.get_logger("edda.api")

VAULT_ENV = "EDDA_VAULT"


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
        request.app.state.kernel = kernel
        log.info("vault_opened", vault=str(vault), title=kernel.title)
    return kernel


KernelDep = Annotated[Kernel, Depends(get_kernel)]


def create_app(vault: Path | None = None, kernel: Kernel | None = None) -> FastAPI:
    """Builds the API for ``vault`` (default: the ``EDDA_VAULT`` environment variable), or around ``kernel``."""
    configure_logging()
    if vault is None and kernel is None and os.environ.get(VAULT_ENV):
        vault = Path(os.environ[VAULT_ENV])
    app = FastAPI(
        title="Edda",
        version="0.1.0",
        summary="Read-only access to an Edda vault: pages, search, sources and links.",
    )
    app.state.vault = vault
    app.state.kernel = kernel

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

    @app.exception_handler(InvalidRequestError)
    async def invalid(_: Request, exc: InvalidRequestError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/health", tags=["meta"])
    def health() -> Health:
        return Health(status="ok")

    @app.get("/api/vault", tags=["meta"])
    def vault_info(kernel: KernelDep) -> VaultInfo:
        return VaultInfo(title=kernel.title, okf_version=okf_version(), pages=len(kernel.pages()))

    @app.get("/api/pages", tags=["pages"])
    def list_pages(
        kernel: KernelDep,
        tag: Annotated[str | None, Query(description="Only pages with this tag")] = None,
        type: Annotated[str | None, Query(description="Only pages of this type")] = None,
    ) -> list[PageSummary]:
        pages = kernel.pages()
        return [p for p in pages if (tag is None or tag in p.tags) and (type is None or p.type == type)]

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

    return app
