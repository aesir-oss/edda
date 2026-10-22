import json
import shutil
from pathlib import Path

import pytest

from edda.kernel import InvalidRequestError, Kernel, KernelError, NotFoundError

pytestmark = pytest.mark.integration


def test_lists_and_reads_pages_with_citations(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    assert kernel.title == "Edda example vault"
    assert [p.id for p in kernel.pages()] == [
        "attention.md",
        "bm25.md",
        "papers/attention-is-all-you-need.md",
        "transformer.md",
    ]
    page = kernel.read_page("attention.md")
    assert page.rev == 1
    assert page.issues == []
    assert page.frontmatter["tags"] == ["ml", "transformers"]
    assert '<a href="/page/transformer.md">' in page.html
    by_id = {c.id: c for c in page.citations}
    assert by_id["notes-attention"].raw == "887e1bdcdcebb311e0cf0502d78b69a47b68ce78e359e22abc3dc8e182c0b1b4"
    assert by_id["vaswani2017-s3-2"].note == "Section 3.2."
    assert by_id["vaswani2017-s3-2"].raw is not None
    assert kernel.read_page("attention.md", link_prefix="#/").html.count('href="#/') == 1


def test_search_links_sources_and_graph(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    assert kernel.search("attention", 1)[0].id == "attention.md"
    assert kernel.links("transformer.md").backlinks == ["attention.md", "papers/attention-is-all-you-need.md"]
    source = kernel.read_source("887e1bdc", 3, 3)
    assert source.text.startswith("- Attention turns a query")
    assert source.title == "Reading notes: attention"
    issues = kernel.graph_issues()
    assert issues.orphans == ["bm25.md"]
    assert issues.broken_links == []
    assert [e.action for e in kernel.history("attention.md")] == ["create_page", "verify"]
    assert kernel.verify_journal() == 8


def test_reports_errors_as_python_exceptions(vault_copy: Path, tmp_path: Path) -> None:
    kernel = Kernel(vault_copy)
    with pytest.raises(NotFoundError):
        kernel.read_page("missing.md")
    with pytest.raises(NotFoundError):
        kernel.links("missing.md")
    with pytest.raises(InvalidRequestError):
        kernel.read_page("../escape.md")
    with pytest.raises(InvalidRequestError):
        kernel.read_source("not-hex")
    with pytest.raises(NotFoundError):
        kernel.read_source("ffff")
    with pytest.raises(KernelError):
        Kernel(tmp_path / "no-vault")


def test_reads_a_hash_chained_journal(vault_copy: Path) -> None:
    lines = [
        {
            "seq": 1,
            "at": "2026-10-21T14:10:00Z",
            "actor": "owner",
            "action": "verify",
            "page": "attention.md",
            "summary": "verified",
            "prev": "0" * 64,
            "hash": "x",
        },
    ]
    journal = vault_copy / "journal" / "000001.jsonl"
    journal.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    kernel = Kernel(vault_copy)
    assert kernel.history("attention.md")[0].action == "verify"
    with pytest.raises(KernelError, match="chain broken"):
        kernel.verify_journal()


def test_rebuilding_the_index_gives_the_same_results(vault_copy: Path) -> None:
    queries = ["attention", "transformer encoder", "rank documents", "translation"]
    kernel = Kernel(vault_copy)
    before = [kernel.search(q) for q in queries]
    graph = kernel.graph_issues()
    del kernel

    shutil.rmtree(vault_copy / ".edda" / "index")
    rebuilt = Kernel(vault_copy)
    assert [rebuilt.search(q) for q in queries] == before
    assert rebuilt.graph_issues() == graph
    rebuilt.refresh_index(force=True)
    assert [rebuilt.search(q) for q in queries] == before


def test_refresh_picks_up_hand_edits(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    page = vault_copy / "wiki" / "bm25.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nZebras are not ranked.\n", encoding="utf-8")
    assert kernel.search("zebra") == []
    kernel.refresh_index()
    assert kernel.search("zebra")[0].id == "bm25.md"


def test_writes_log_md_from_the_journal(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    log_md = vault_copy / "wiki" / "log.md"
    committed = log_md.read_text(encoding="utf-8")
    log_md.unlink()
    assert kernel.write_log() == 8
    assert log_md.read_text(encoding="utf-8") == committed
    page = kernel.read_page("log.md")
    assert page.issues == []
    assert '<a href="/page/attention.md">Attention mechanism</a>' in page.html
