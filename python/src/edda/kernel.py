"""Typed access to the kernel's read-side system calls."""

from pathlib import Path

from pydantic import TypeAdapter

from edda import _kernel
from edda._kernel import InvalidRequestError, KernelError, NotFoundError
from edda.models import GraphIssues, JournalEntry, Links, Page, PageSummary, SearchHit, Source

__all__ = ["InvalidRequestError", "Kernel", "KernelError", "NotFoundError"]

_summaries = TypeAdapter(list[PageSummary])
_hits = TypeAdapter(list[SearchHit])
_entries = TypeAdapter(list[JournalEntry])


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

    def links(self, page_id: str) -> Links:
        """A page's outgoing links and backlinks."""
        return Links.model_validate_json(self._kernel.links(page_id))

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
