from pathlib import Path
from typing import Any

import pytest

from edda import review
from edda.kernel import InvalidRequestError, Kernel
from edda.models import PageRevision, TextEdit

pytestmark = pytest.mark.integration

PAPER = "d9ca93c3"


def _zebras(kernel: Kernel) -> str:
    edits = [TextEdit(old="orphan.", new="orphan. Zebras graze in herds.")]
    proposal = kernel.propose_edit("ingest:test", "bm25.md", 1, edits, "adds zebras")
    kernel.cite(proposal.id, "graze in herds.", PAPER, "Section 6")
    return proposal.id


def test_accepts_as_proposed_and_rejects(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    decision = review.accept(kernel, _zebras(kernel))
    assert (decision.proposal.status, decision.entry.rev, decision.revised_from) == ("accepted", 2, None)

    other = kernel.flag("lint:test", "bm25.md", "gap", "No worked example.")
    decision = review.reject(kernel, other.id, "covered elsewhere")
    assert (decision.proposal.status, decision.entry.action) == ("rejected", "reject_proposal")


def test_a_revised_edit_keeps_the_citations_whose_claims_survive(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    original = _zebras(kernel)
    revised = [TextEdit(old="orphan.", new="orphan. Wild zebras graze in herds. I saw some.")]
    decision = review.accept(kernel, original, edits=revised)

    assert decision.revised_from == original
    assert decision.proposal.job == "owner"
    assert decision.proposal.rationale == f"Revised from {original}: adds zebras"
    assert [c.claim for c in decision.proposal.citations] == ["graze in herds."]
    assert kernel.proposal(original).status == "rejected"
    page = kernel.read_page("bm25.md")
    assert "Wild zebras graze in herds.[^" in page.body
    assert "I saw some." in page.body
    assert page.frontmatter["verified"][-1]["by"] == "owner"
    summaries = [e.summary for e in kernel.history("bm25.md")][-2:]
    assert summaries[0].startswith("Revised from")
    assert summaries[1] == f"rejected {original}: revised as {decision.proposal.id}"


def test_a_revised_new_page_keeps_the_model_text_left_out(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    proposal = kernel.propose_page(
        "ingest:test",
        "multi-head-attention.md",
        type="Concept",
        title="Multi-head attention",
        description="Several attention heads side by side.",
        tags=["ml"],
        body="Several heads run in parallel, see [attention](/attention.md).\n",
        rationale="worth its own page",
    )
    kernel.cite(proposal.id, "run in parallel", PAPER, "Section 3.2.2")
    decision = review.accept(kernel, proposal.id, page=PageRevision(title="Multi-head attention (MHA)"))
    page = kernel.read_page("multi-head-attention.md")
    assert page.title == "Multi-head attention (MHA)"
    assert page.frontmatter["description"] == "Several attention heads side by side."
    assert page.frontmatter["tags"] == ["ml"]
    assert len(decision.proposal.citations) == 1


def test_bad_revisions_leave_the_original_pending(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    original = _zebras(kernel)
    with pytest.raises(InvalidRequestError, match="revise an edit with edits"):
        review.accept(kernel, original, page=PageRevision(title="x"))
    with pytest.raises(InvalidRequestError, match="0 times"):
        review.accept(kernel, original, edits=[TextEdit(old="no such text", new="x")])
    broken = [TextEdit(old="orphan.", new="orphan. See [nothing](/missing.md).")]
    with pytest.raises(InvalidRequestError, match=r"missing\.md"):
        review.accept(kernel, original, edits=broken)
    assert kernel.proposal(original).status == "pending"
    failed = [p for p in kernel.proposals() if p.job == "owner"]
    assert [p.status for p in failed] == ["rejected"]

    review.reject(kernel, original, "no")
    with pytest.raises(InvalidRequestError, match="is rejected, not pending"):
        review.accept(kernel, original, edits=[TextEdit(old="orphan.", new="orphan!")])


def test_writes_are_reported_to_listeners(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    heard: list[tuple[str, dict[str, Any]]] = []
    kernel.listen(lambda event, data: heard.append((event, data)))
    proposal = _zebras(kernel)
    kernel.accept(proposal)
    kernel.accept(kernel.propose_edit("owner", "bm25.md", 2, [TextEdit(old="Zebras", new="Lions")], "lions").id)
    kernel.rollback("bm25.md", 2)
    kernel.add_source(b"# Zebras\n", extension="md", title="Zebras")
    assert [e for e, _ in heard] == ["proposal"] * 5 + ["page", "source"]
    assert heard[5][1] == {"id": "bm25.md", "rev": 4}
    assert heard[0][1] == {"id": proposal, "status": "pending", "page": "bm25.md", "job": "ingest:test"}
    assert heard[2][1] == {"id": proposal, "status": "accepted", "page": "bm25.md", "job": None}
    assert heard[6][1]["added"] is True
