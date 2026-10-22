"""The shapes the kernel's read-side system calls return, shared by the Python API and its OpenAPI schema."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class PageSummary(_Model):
    """A page as the Library lists it."""

    id: str
    title: str
    description: str | None = None
    type: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: str


class SearchHit(_Model):
    """One search result; ``snippet`` is escaped HTML with matches in ``<b>``."""

    id: str
    title: str
    description: str
    score: float
    snippet: str


class Citation(_Model):
    """A page's source, with the footnote that cites it and the raw file it resolves to."""

    id: str
    resource: str
    title: str | None = None
    note: str | None = None
    raw: str | None = None
    cited: bool


class Page(_Model):
    """A page: frontmatter, Markdown body, rendered HTML, revision and citations."""

    id: str
    title: str
    rev: int | None = None
    frontmatter: dict[str, Any]
    body: str
    html: str
    citations: list[Citation]
    issues: list[str]


class Source(_Model):
    """The text of a raw source, or of a span of its lines."""

    hash: str
    title: str | None = None
    url: str | None = None
    added: str | None = None
    driver: str | None = None
    text: str


class OutLink(_Model):
    """A link out of a page; ``target`` is the page id for links inside the bundle."""

    dest: str
    target: str | None = None


class Links(_Model):
    """A page's outgoing links and the pages that link to it."""

    outgoing: list[OutLink]
    backlinks: list[str]


class BrokenLink(_Model):
    """A link to a page that does not exist."""

    from_: str = Field(alias="from")
    dest: str


class GraphIssues(_Model):
    """Pages nothing links to, and links to pages that do not exist."""

    orphans: list[str]
    broken_links: list[BrokenLink]


class JournalEntry(_Model):
    """One recorded change to the vault."""

    seq: int
    at: str
    actor: str
    action: str
    page: str | None = None
    rev: int | None = None
    summary: str
    data: Any = None
    prev: str
    hash: str
