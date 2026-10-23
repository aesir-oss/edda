import json
from pathlib import Path

import pytest
from docling_core.types.doc.document import DoclingDocument
from fastapi.testclient import TestClient

from edda.api import create_app
from edda.drivers import import_document
from edda.drivers.document import Converted, paged_markdown
from edda.jobs.ingest import _readable_source, ingest_tools
from edda.kernel import InvalidRequestError, Kernel
from edda.models import TextEdit

pytestmark = pytest.mark.integration

RECORDED = Path(__file__).parents[1] / "fixtures" / "docling" / "text.json"


def lantern(_: bytes, __: str) -> Converted:
    """Docling's recorded conversion of the two-page Lantern PDF."""
    document = DoclingDocument.model_validate(json.loads(RECORDED.read_text(encoding="utf-8"))["document"])
    return Converted(text=paged_markdown(document), converter="docling 2.96.1", method="layout")


def test_a_pdf_lands_as_its_original_and_paged_markdown_that_can_be_cited_by_page(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    imported = import_document(kernel, b"%PDF-1.7 the original bytes", filename="lantern.pdf", convert=lantern)
    original, derived = imported.original.source, imported.derived.source
    assert (vault_copy / "raw" / f"{original.hash}.pdf").read_bytes() == b"%PDF-1.7 the original bytes"
    listed = {s.hash: s for s in kernel.sources()}
    assert listed[derived.hash].derived_from == original.hash
    assert listed[derived.hash].method == "layout"
    source = kernel.read_source(derived.hash, page=2)
    assert source.text.startswith("<!-- page 2 -->\n\nHistory")
    assert (source.pages, source.converter, source.derived_from) == (2, "docling 2.96.1", original.hash)
    with pytest.raises(InvalidRequestError, match="has no page 3; its pages are 1 to 2"):
        kernel.read_source(derived.hash, page=3)

    edits = [TextEdit(old="orphan.", new="orphan. Lantern was drafted in 2019.")]
    proposal = kernel.propose_edit("ingest:test", "bm25.md", 1, edits, "adds Lantern")
    with pytest.raises(InvalidRequestError, match="has no page 5"):
        kernel.cite(proposal.id, "drafted in 2019", derived.hash, "page 5")
    cited = kernel.cite(proposal.id, "drafted in 2019", derived.hash, "page 2")
    assert cited.preview.issues == []

    assert _readable_source(kernel, original.hash[:10]) == derived.hash
    assert _readable_source(kernel, derived.hash) == derived.hash
    read = next(t for t in ingest_tools(kernel, "j", derived.hash, []) if t.tool.name == "read_source")
    assert read.run({"page": 1}).startswith("<!-- page 1 -->")

    again = import_document(kernel, b"%PDF-1.7 the original bytes", filename="lantern.pdf", convert=lantern)
    assert (again.original.added, again.derived.added) == (False, False)


def test_a_document_without_markdown_cannot_be_ingested(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    added = kernel.add_source(b"%PDF-1.7 alone", extension="pdf", title="Alone", driver="document")
    with pytest.raises(InvalidRequestError, match="no Markdown made from it"):
        _readable_source(kernel, added.source.hash)


def test_uploads_an_office_file_through_the_api(vault_copy: Path) -> None:
    from tests.unit.test_document_driver import _docx

    client = TestClient(create_app(vault=vault_copy))
    first = client.post("/api/sources/document", params={"filename": "notes.docx"}, content=_docx())
    assert first.status_code == 201
    body = first.json()
    assert body["derived"]["source"]["derived_from"] == body["original"]["source"]["hash"]
    assert body["derived"]["source"]["title"] == "Lantern protocol"
    assert client.post("/api/sources/document", params={"filename": "notes.docx"}, content=_docx()).status_code == 200
    text = client.get(f"/api/sources/{body['derived']['source']['hash']}").json()
    assert (text["method"], text["text"].splitlines()[0]) == ("native", "# Lantern protocol")
    refused = client.post("/api/sources/document", params={"filename": "notes.txt"}, content=b"hi")
    assert refused.status_code == 400
    assert client.post("/api/sources/document", content=b"PK").status_code == 422


def test_refuses_oversized_documents(vault_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("edda.api.app.MAX_DOCUMENT_BYTES", 10)
    client = TestClient(create_app(vault=vault_copy))
    assert (
        client.post("/api/sources/document", params={"filename": "a.pdf"}, content=b"%PDF" + b"x" * 20).status_code
        == 413
    )
