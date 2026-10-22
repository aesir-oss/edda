"""Context assembly for jobs: which pages go into the prompt, within a token budget.

The kernel ranks candidate pages (BM25 similarity to the source, then link neighbours); this module fills the budget
in that order. The fixed parts (the job's instructions and the vault's ``index.md``) come first and stay byte-stable
between calls, so providers with a prompt cache can reuse them. Tokens are estimated at four characters each, which
is close enough for budgeting and needs no call to a tokenizer or an API.
"""

import math

from pydantic import BaseModel, ConfigDict

from edda.kernel import Kernel

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """A rough token count for ``text``."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


class ContextOverflow(ValueError):
    """The fixed parts and the source alone do not fit the budget. Nothing is truncated silently."""


class PackedPage(BaseModel):
    """A page as it appears in the prompt."""

    model_config = ConfigDict(frozen=True)

    id: str
    rev: int | None
    text: str


class Context(BaseModel):
    """What a job sends: a stable system prompt, then the related pages and the source."""

    model_config = ConfigDict(frozen=True)

    system: str
    pages: list[PackedPage]
    source: str
    omitted: list[str]
    tokens: int

    def user_text(self) -> str:
        """The pages, then the source, as the first user message."""
        pages = "\n\n".join(p.text for p in self.pages) or "(no related pages)"
        return f"# Related pages\n\n{pages}\n\n# The new source\n\n{self.source}"


def render_page(kernel: Kernel, page_id: str) -> PackedPage:
    """A page with what a job needs to edit it: its id, revision, title, source ids and Markdown body."""
    page = kernel.read_page(page_id)
    sources = ", ".join(f"{c.id} ({c.resource})" for c in page.citations) or "none"
    header = f"## /{page.id} (revision {page.rev or 0}): {page.title}\nSources: {sources}"
    return PackedPage(id=page.id, rev=page.rev, text=f"{header}\n\n{page.body.strip()}")


def pack(kernel: Kernel, *, instructions: str, source: str, budget: int, k: int = 20) -> Context:
    """Builds a job's context: instructions and ``index.md``, then related pages by rank while they fit, then the source.

    A page that does not fit is skipped, and a smaller one further down the ranking may still be included; every
    skipped page is listed in ``omitted``.

    Raises:
        ContextOverflow: The instructions, the index and the source alone exceed ``budget``.
    """
    index = kernel.read_page("index.md").body.strip()
    system = f"{instructions.strip()}\n\n# The wiki's index\n\n{index}"
    used = estimate_tokens(system) + estimate_tokens(source)
    if used > budget:
        raise ContextOverflow(
            f"the instructions, index and source need about {used} tokens, over the budget of {budget}; raise the "
            "budget in edda.toml or split the source"
        )
    pages: list[PackedPage] = []
    omitted: list[str] = []
    for related in kernel.related(source, k):
        page = render_page(kernel, related.id)
        cost = estimate_tokens(page.text)
        if used + cost <= budget:
            pages.append(page)
            used += cost
        else:
            omitted.append(page.id)
    return Context(system=system, pages=pages, source=source, omitted=omitted, tokens=used)
