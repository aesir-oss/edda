"""The shapes the kernel's read-side system calls return, shared by the Python API and its OpenAPI schema."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class PageSummary(_Model):
    """A page as the Library lists it; ``sources`` are the hashes of the raw sources it cites."""

    id: str
    title: str
    description: str | None = None
    type: str | None = None
    tags: list[str] = Field(default_factory=list)
    status: str
    stale_after: str | None = None
    stale: bool = False
    sources: list[str] = Field(default_factory=list)


class SourceSummary(_Model):
    """A raw source as the Library's source filter lists it."""

    hash: str
    title: str | None = None
    url: str | None = None
    added: str | None = None
    driver: str | None = None


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


class TextEdit(_Model):
    """Replace ``old``, which must appear exactly once in the page body, with ``new``."""

    old: str
    new: str


class EditChange(_Model):
    """``propose_edit``: text edits to an existing page, made against revision ``base_rev``."""

    kind: Literal["edit"]
    page: str
    base_rev: int
    edits: list[TextEdit]


class NewPageChange(_Model):
    """``propose_page``: a new page, created as a draft."""

    kind: Literal["new_page"]
    page: str
    type: str
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    body: str


class FlagChange(_Model):
    """``flag``: a contradiction, outdated claim or gap, for review. It changes no text."""

    kind: Literal["flag"]
    page: str
    flag: str
    note: str


class ProposalCitation(_Model):
    """A claim in a proposal's added text, backed by a span of a raw source."""

    id: str
    claim: str
    raw: str
    resource: str
    title: str | None = None
    locator: str


class ProposalPreview(_Model):
    """What accepting the proposal would do, and the kernel rules that keep it from being applied."""

    diff: str
    issues: list[str]


class Proposal(_Model):
    """A change a job (or the owner) submitted, waiting for review."""

    id: str
    job: str
    created: str
    change: Annotated[EditChange | NewPageChange | FlagChange, Field(discriminator="kind")]
    citations: list[ProposalCitation] = Field(default_factory=list)
    rationale: str
    status: Literal["pending", "accepted", "rejected"]
    preview: ProposalPreview


class AddedSource(_Model):
    """A source after an import; ``added`` is false when the same bytes were already stored."""

    source: SourceSummary
    added: bool


class Related(_Model):
    """A page related to a text; ``via`` says whether its score came from its own match or from a link."""

    id: str
    title: str
    score: float
    via: Literal["search", "link"]


class PageRevision(_Model):
    """The owner's changes to a proposed new page; fields left out keep the model's text."""

    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    body: str | None = None


class AcceptRequest(_Model):
    """Accept a proposal as it is, or after the owner revised it: ``edits`` for an edit, ``page`` for a new page."""

    edits: list[TextEdit] | None = None
    page: PageRevision | None = None


class RejectRequest(_Model):
    """Why the owner turned a proposal down."""

    reason: str = Field(min_length=1)


class Decision(_Model):
    """The decided proposal, the journal entry the kernel wrote, and the proposal it revised, if any."""

    proposal: Proposal
    entry: JournalEntry
    revised_from: str | None = None


class JobRun(_Model):
    """A job started from the API and where it stands."""

    id: str
    job: str
    target: str
    state: Literal["queued", "running", "done", "failed"]
    queued: str
    started: str | None = None
    finished: str | None = None
    proposals: list[str] = Field(default_factory=list)
    error: str | None = None


class IngestRequest(_Model):
    """The raw source to ingest, by full hash or unique prefix."""

    source: str = Field(min_length=1)


class WebImportRequest(_Model):
    """A web page to fetch and store as a raw source."""

    url: str = Field(min_length=1)
    title: str | None = None
