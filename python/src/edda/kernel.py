"""Typed access to the kernel's read-side system calls."""

from pathlib import Path

from pydantic import TypeAdapter

from edda import _kernel
from edda._kernel import ConflictError, InvalidRequestError, KernelError, NotFoundError
from edda.models import (
    AddedSource,
    GraphIssues,
    JournalEntry,
    Links,
    Page,
    PageSummary,
    Proposal,
    SearchHit,
    Source,
    SourceSummary,
    TextEdit,
)

__all__ = ["ConflictError", "InvalidRequestError", "Kernel", "KernelError", "NotFoundError"]

_summaries = TypeAdapter(list[PageSummary])
_hits = TypeAdapter(list[SearchHit])
_entries = TypeAdapter(list[JournalEntry])
_sources = TypeAdapter(list[SourceSummary])
_proposals = TypeAdapter(list[Proposal])
_edits = TypeAdapter(list[TextEdit])


class Kernel:
    """A vault opened for reading. Opening it builds or refreshes ``.edda/index``."""

    def __init__(self, vault: Path) -> None:
        """Opens the vault at ``vault``.

        Raises:
            KernelError: The folder is not a vault, or a page in it cannot be read.
        """
        self.vault = vault
        self._kernel = _kernel.Kernel(str(vault))

    @property
    def title(self) -> str:
        """The vault's title from ``edda.toml``."""
        return self._kernel.title

    def pages(self) -> list[PageSummary]:
        """Every page in the bundle, in id order."""
        return _summaries.validate_json(self._kernel.pages())

    def search(self, query: str, k: int = 10) -> list[SearchHit]:
        """The ``k`` best pages for ``query``, best first."""
        return _hits.validate_json(self._kernel.search(query, k))

    def read_page(self, page_id: str, link_prefix: str = "/page/") -> Page:
        """A page with its HTML; links to other pages are rewritten to start with ``link_prefix``."""
        return Page.model_validate_json(self._kernel.read_page(page_id, link_prefix))

    def read_source(self, hash: str, start: int | None = None, end: int | None = None) -> Source:
        """A raw source by full hash or unique prefix, or lines ``start`` to ``end`` of it (1-based, inclusive)."""
        return Source.model_validate_json(self._kernel.read_source(hash, start, end))

    def sources(self) -> list[SourceSummary]:
        """Every raw source, by hash."""
        return _sources.validate_json(self._kernel.sources())

    def links(self, page_id: str) -> Links:
        """A page's outgoing links and backlinks."""
        return Links.model_validate_json(self._kernel.links(page_id))

    def propose_edit(self, job: str, page_id: str, base_rev: int, edits: list[TextEdit], rationale: str) -> Proposal:
        """``propose_edit``: text edits to a page, made against its revision ``base_rev``.

        Raises:
            ConflictError: The page is no longer at ``base_rev``.
            InvalidRequestError: An edit's ``old`` text does not appear exactly once.
        """
        edits_json = _edits.dump_json(edits).decode()
        return Proposal.model_validate_json(self._kernel.propose_edit(job, page_id, base_rev, edits_json, rationale))

    def propose_page(
        self,
        job: str,
        page_id: str,
        *,
        type: str,
        title: str,
        description: str,
        body: str,
        rationale: str,
        tags: list[str] | None = None,
    ) -> Proposal:
        """``propose_page``: a new page, created as a draft.

        Raises:
            InvalidRequestError: The page already exists, or its id is not valid.
        """
        raw = self._kernel.propose_page(job, page_id, type, title, description, tags or [], body, rationale)
        return Proposal.model_validate_json(raw)

    def cite(self, proposal_id: str, claim: str, source: str, locator: str, source_id: str | None = None) -> Proposal:
        """``cite``: back ``claim``, text the proposal adds, with ``locator`` in the raw source ``source``.

        Raises:
            InvalidRequestError: The claim is not in the added text, or the proposal is no longer pending.
            NotFoundError: No raw source or proposal by that name.
        """
        return Proposal.model_validate_json(self._kernel.cite(proposal_id, claim, source, locator, source_id))

    def flag(self, job: str, page_id: str, kind: str, note: str) -> Proposal:
        """``flag``: report a contradiction, outdated claim or gap on a page, for review."""
        return Proposal.model_validate_json(self._kernel.flag(job, page_id, kind, note))

    def add_source(
        self,
        data: bytes,
        *,
        extension: str | None = None,
        title: str | None = None,
        url: str | None = None,
        driver: str | None = None,
        by: str = "owner",
    ) -> AddedSource:
        """Stores ``data`` in ``raw/`` under its SHA-256, journals it and refreshes the index.

        Adding bytes that are already stored changes nothing and returns the stored source with ``added`` false.
        """
        raw = self._kernel.add_source(data, extension, title, url, driver, by)
        return AddedSource.model_validate_json(raw)

    def accept(self, proposal_id: str, by: str = "owner") -> JournalEntry:
        """Accepts a pending proposal on behalf of ``by`` and applies it; returns the journal entry it wrote.

        Raises:
            ConflictError: The page changed since the proposal's base revision.
            InvalidRequestError: The proposal is not pending, or breaks a kernel rule (such as uncited text).
        """
        return JournalEntry.model_validate_json(self._kernel.accept(proposal_id, by))

    def reject(self, proposal_id: str, reason: str, by: str = "owner") -> JournalEntry:
        """Rejects a pending proposal; the page is untouched. Returns the journal entry it wrote."""
        return JournalEntry.model_validate_json(self._kernel.reject(proposal_id, reason, by))

    def proposals(self) -> list[Proposal]:
        """Every proposal with its preview, oldest first."""
        return _proposals.validate_json(self._kernel.proposals())

    def proposal(self, proposal_id: str) -> Proposal:
        """One proposal with its preview."""
        return Proposal.model_validate_json(self._kernel.proposal(proposal_id))

    def graph_issues(self) -> GraphIssues:
        """Orphan pages and broken links."""
        return GraphIssues.model_validate_json(self._kernel.graph_issues())

    def history(self, page_id: str) -> list[JournalEntry]:
        """The journal entries for a page, oldest first."""
        return _entries.validate_json(self._kernel.history(page_id))

    def write_log(self) -> int:
        """Regenerates ``log.md`` from the journal; returns the number of entries it lists."""
        return self._kernel.write_log()

    def verify_journal(self) -> int:
        """Checks the journal's hash chain; returns the number of entries."""
        return self._kernel.verify_journal()

    def refresh_index(self, force: bool = False) -> None:
        """Rebuilds the index if the files changed, or always when ``force`` is true."""
        self._kernel.refresh_index(force)
