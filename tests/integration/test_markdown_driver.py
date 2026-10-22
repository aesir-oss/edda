import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from edda.api import create_app
from edda.drivers import import_markdown
from edda.kernel import Kernel

pytestmark = pytest.mark.integration

NOTE = b"# Tokenizers\n\nBM25 ranks terms after tokenizing and stemming them.\n"


def test_imports_a_file_unedited_and_journals_it_once(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    added = import_markdown(kernel, NOTE, filename="tok.md")
    digest = hashlib.sha256(NOTE).hexdigest()
    assert (added.added, added.source.hash, added.source.title) == (True, digest, "Tokenizers")
    assert (vault_copy / "raw" / f"{digest}.md").read_bytes() == NOTE
    meta = (vault_copy / "raw" / f"{digest}.meta.toml").read_text(encoding="utf-8")
    assert 'driver = "markdown"' in meta
    assert 'added = "' in meta
    assert kernel.verify_journal() == 9
    assert "added source Tokenizers" in (vault_copy / "wiki" / "log.md").read_text(encoding="utf-8")
    assert digest in [s.hash for s in kernel.sources()]
    assert kernel.read_source(digest[:8]).text == NOTE.decode()

    again = import_markdown(kernel, NOTE, filename="other.md", title="Ignored")
    assert (again.added, again.source.title) == (False, "Tokenizers")
    assert kernel.verify_journal() == 9


def test_upload_through_the_api(vault_copy: Path) -> None:
    client = TestClient(create_app(vault=vault_copy))
    response = client.post("/api/sources/markdown", params={"filename": "tok.md"}, content=NOTE)
    assert response.status_code == 201
    assert response.json()["source"]["driver"] == "markdown"
    assert client.post("/api/sources/markdown", content=NOTE).status_code == 200
    assert len(client.get("/api/sources").json()) == 4
