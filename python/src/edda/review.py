"""The owner's decisions on proposals: accept, accept after revising, reject."""

import structlog

from edda.kernel import InvalidRequestError, Kernel, KernelError
from edda.models import Decision, EditChange, NewPageChange, PageRevision, Proposal, TextEdit

log = structlog.get_logger("edda.review")

OWNER = "owner"


def accept(
    kernel: Kernel,
    proposal_id: str,
    *,
    edits: list[TextEdit] | None = None,
    page: PageRevision | None = None,
    by: str = OWNER,
) -> Decision:
    """Accepts a proposal, or the owner's revision of it when ``edits`` or ``page`` is given.

    A revision becomes a new proposal by the owner, made against the same base revision. Citations whose claim is
    still in the revised text carry over; the owner's own words need none. The revision is applied and the original
    rejected as revised; if the revision cannot be applied, it is rejected and the original stays pending.

    Raises:
        ConflictError: The page changed since the proposal's base revision.
        InvalidRequestError: The proposal is not pending, a revision does not fit its kind, or the kernel blocked it.
        NotFoundError: No proposal by that id.
    """
    if edits is None and page is None:
        entry = kernel.accept(proposal_id, by)
        return Decision(proposal=kernel.proposal(proposal_id), entry=entry)
    with kernel.writes:
        original = kernel.proposal(proposal_id)
        if original.status != "pending":
            raise InvalidRequestError(f"proposal {proposal_id} is {original.status}, not pending")
        revision = _propose_revision(kernel, original, edits, page)
        try:
            entry = kernel.accept(revision.id, by)
        except KernelError as exc:
            kernel.reject(revision.id, f"could not apply the revision: {exc}", by)
            raise
        kernel.reject(proposal_id, f"revised as {revision.id}", by)
        log.info("proposal_revised", proposal=proposal_id, revision=revision.id)
        return Decision(proposal=kernel.proposal(revision.id), entry=entry, revised_from=proposal_id)


def reject(kernel: Kernel, proposal_id: str, reason: str, by: str = OWNER) -> Decision:
    """Rejects a pending proposal; the page is untouched."""
    entry = kernel.reject(proposal_id, reason, by)
    return Decision(proposal=kernel.proposal(proposal_id), entry=entry)


def _propose_revision(
    kernel: Kernel, original: Proposal, edits: list[TextEdit] | None, page: PageRevision | None
) -> Proposal:
    change = original.change
    rationale = f"Revised from {original.id}: {original.rationale}"
    if isinstance(change, EditChange) and page is None and edits:
        revision = kernel.propose_edit(OWNER, change.page, change.base_rev, edits, rationale)
        added = [e.new for e in edits]
    elif isinstance(change, NewPageChange) and edits is None and page is not None:
        title = page.title if page.title is not None else change.title
        description = page.description if page.description is not None else change.description
        body = page.body if page.body is not None else change.body
        revision = kernel.propose_page(
            OWNER,
            change.page,
            type=change.type,
            title=title,
            description=description,
            body=body,
            rationale=rationale,
            tags=page.tags if page.tags is not None else change.tags,
        )
        added = [title, description, body]
    else:
        raise InvalidRequestError(
            f"proposal {original.id} is a {change.kind}: revise an edit with edits and a new page with page"
        )
    for citation in original.citations:
        if any(citation.claim in text for text in added):
            revision = kernel.cite(revision.id, citation.claim, citation.raw, citation.locator, citation.id)
    return revision
