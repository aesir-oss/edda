from pathlib import Path

import pytest

from edda.kernel import ConflictError, InvalidRequestError, Kernel, NotFoundError
from edda.models import EditChange, FlagChange, NewPageChange, TextEdit

pytestmark = pytest.mark.integration

PAPER = "d9ca93c3"


def test_an_edit_is_proposed_cited_and_previewed(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    added = "self-attention.[^notes-attention]\n\nThe decoder masks future positions."
    edits = [TextEdit(old="self-attention.[^notes-attention]", new=added)]
    proposal = kernel.propose_edit("ingest:test", "attention.md", 1, edits, "adds masking")
    assert proposal.id == "prop_000001"
    assert isinstance(proposal.change, EditChange)
    assert proposal.preview.issues == ['added text cites no source: "The decoder masks future positions."']

    cited = kernel.cite(proposal.id, "masks future positions.", PAPER, "Section 3.2.3")
    assert cited.citations[0].id == "notes-on-attention-is-all-you-need-section-3-2-3"
    assert cited.citations[0].resource == "https://arxiv.org/abs/1706.03762"
    assert cited.preview.issues == []
    assert (
        "+The decoder masks future positions.[^notes-on-attention-is-all-you-need-section-3-2-3]" in cited.preview.diff
    )
    assert kernel.proposals() == [cited]
    assert kernel.proposal(proposal.id) == cited
    page = (vault_copy / "wiki" / "attention.md").read_text(encoding="utf-8")
    assert "masks future" not in page, "proposing must not write the page"


def test_new_pages_and_flags(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    page = kernel.propose_page(
        "ingest:test",
        "multi-head-attention.md",
        type="Concept",
        title="Multi-head attention",
        description="Several attention heads side by side.",
        tags=["ml"],
        body="Several heads run in parallel, see [attention](/attention.md).\n",
        rationale="worth its own page",
    )
    assert isinstance(page.change, NewPageChange)
    cited = kernel.cite(page.id, "run in parallel", PAPER, "Section 3.2.2", source_id="vaswani2017-s3-2-2")
    assert cited.preview.issues == []
    assert "+status: draft" in cited.preview.diff

    flag = kernel.flag("lint:test", "bm25.md", "gap", "No worked example.")
    assert isinstance(flag.change, FlagChange)
    assert flag.preview.diff == ""


def test_refuses_bad_proposals(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    with pytest.raises(ConflictError, match="revision 1, not 0"):
        kernel.propose_edit("j", "attention.md", 0, [], "stale")
    with pytest.raises(InvalidRequestError, match="0 times"):
        kernel.propose_edit("j", "attention.md", 1, [TextEdit(old="no such text", new="x")], "r")
    with pytest.raises(InvalidRequestError, match="already exists"):
        kernel.propose_page("j", "bm25.md", type="Concept", title="t", description="d", body="b", rationale="r")
    with pytest.raises(NotFoundError):
        kernel.flag("j", "missing.md", "gap", "n")
    with pytest.raises(NotFoundError):
        kernel.proposal("prop_000042")
    proposal = kernel.propose_edit("j", "bm25.md", 1, [TextEdit(old="orphan.", new="orphan. More.")], "r")
    with pytest.raises(InvalidRequestError, match="not in the text"):
        kernel.cite(proposal.id, "absent", PAPER, "p1")
