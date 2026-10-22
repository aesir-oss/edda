import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from edda.api import create_app
from edda.api.openapi import main, schema_json

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(vault_copy: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("EDDA_VAULT", str(vault_copy))
    return TestClient(create_app())


def test_browse_search_and_read_the_example_vault(client: TestClient) -> None:
    assert client.get("/api/vault").json()["pages"] == 4
    assert [p["id"] for p in client.get("/api/pages", params={"tag": "search"}).json()] == ["bm25.md"]
    hits = client.get("/api/search", params={"q": "positional sine encodings"}).json()
    assert hits[0]["id"] == "transformer.md"
    page = client.get("/api/pages/papers/attention-is-all-you-need.md").json()
    assert page["title"] == "Attention Is All You Need"
    assert page["frontmatter"]["paper_year"] == 2017
    assert client.get("/api/links/attention.md").json()["backlinks"] == [
        "papers/attention-is-all-you-need.md",
        "transformer.md",
    ]
    source = client.get("/api/sources/d9ca93c3").json()
    assert source["url"] == "https://arxiv.org/abs/1706.03762"
    assert client.get("/api/graph/issues").json()["orphans"] == ["bm25.md"]
    assert client.get("/api/pages/nope.md").status_code == 404
    assert client.get("/api/pages/..%2Fsecret.md").status_code in (400, 404)


def test_committed_schema_matches_the_api(tmp_path: Path) -> None:
    committed = REPO_ROOT / "web" / "openapi.json"
    assert committed.read_text(encoding="utf-8") == schema_json(), "run `pixi run gen-api`"
    out = tmp_path / "openapi.json"
    assert main([str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["info"]["title"] == "Edda"
