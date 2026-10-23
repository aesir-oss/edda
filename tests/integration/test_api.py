import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from edda.api import create_app
from edda.api.app import get_today
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


def test_filters_the_example_vault_by_source_status_and_staleness(client: TestClient) -> None:
    def ids(**params: str) -> list[str]:
        return [p["id"] for p in client.get("/api/pages", params=params).json()]

    assert ids(source="887e1bdc") == ["attention.md"]
    assert ids(source="d9ca93c3") == ["attention.md", "papers/attention-is-all-you-need.md", "transformer.md"]
    assert ids(status="draft") == ["bm25.md"]
    assert ids(stale="true") == []
    client.app.dependency_overrides[get_today] = lambda: date(2028, 1, 1)  # type: ignore[attr-defined]
    assert ids(stale="true") == ["bm25.md"]
    sources = client.get("/api/sources").json()
    assert [s["title"] for s in sources] == [
        "Reading notes: attention",
        "BM25 in one page",
        "Notes on Attention Is All You Need",
    ]


def test_a_job_proposes_and_the_owner_revises_and_accepts_over_http(vault_copy: Path) -> None:
    from edda.jobs.background import Progress
    from edda.kernel import Kernel
    from edda.models import JobOutcome, TextEdit

    def ingest(kernel: Kernel, source: str, progress: Progress) -> JobOutcome:
        edits = [TextEdit(old="orphan.", new="orphan. Zebras graze in herds.")]
        proposal = kernel.propose_edit("ingest:test", "bm25.md", 1, edits, "adds zebras")
        kernel.cite(proposal.id, "graze in herds.", source, "Section 6")
        return JobOutcome(proposals=[proposal.id])

    app = create_app(vault=vault_copy, ingest=ingest)
    heard: list[tuple[str, dict[str, object]]] = []
    app.state.events.publish = lambda event, data: heard.append((event, data))
    client = TestClient(app)
    run = client.post("/api/jobs/ingest", json={"source": "d9ca93c3"}).json()
    app.state.jobs.wait(5)
    [proposal_id] = client.get(f"/api/jobs/{run['id']}").json()["proposals"]
    pending = client.get("/api/proposals", params={"status": "pending"}).json()
    assert [p["id"] for p in pending] == [proposal_id]

    revised = [{"old": "orphan.", "new": "orphan. Wild zebras graze in herds."}]
    decision = client.post(f"/api/proposals/{proposal_id}/accept", json={"edits": revised}).json()
    assert decision["revised_from"] == proposal_id
    assert "Wild zebras" in client.get("/api/pages/bm25.md").json()["body"]
    assert client.post(f"/api/proposals/{proposal_id}/accept").status_code == 400
    assert [e for e, _ in heard if e == "job"] == ["job", "job", "job"]
    decided = [(d["id"], d["status"]) for e, d in heard if e == "proposal" and d["job"] is None]
    assert decided == [(decision["proposal"]["id"], "accepted"), (proposal_id, "rejected")]


def test_imports_a_web_page_the_policy_allows(vault_copy: Path) -> None:
    import httpx

    html = (REPO_ROOT / "tests" / "fixtures" / "bm25.html").read_bytes()
    web = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=html, headers={"content-type": "text/html"})
        )
    )
    client = TestClient(create_app(vault=vault_copy, web_client=web))
    url = "https://en.wikipedia.org/wiki/Okapi_BM25"
    first = client.post("/api/sources/web", json={"url": url})
    assert first.status_code == 201
    source = first.json()["source"]
    assert (source["url"], source["driver"], source["title"]) == (url, "web", "Okapi BM25")
    assert client.post("/api/sources/web", json={"url": url}).status_code == 200
    text = client.get(f"/api/sources/{source['hash']}").json()["text"]
    assert text.startswith("# Okapi BM25")

    refused = client.post("/api/sources/web", json={"url": "https://example.com/"})
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "example.com is not in [network] allow in policy.toml",
    )
    (vault_copy / "policy.toml").write_text("[network\n")
    assert client.post("/api/sources/web", json={"url": url}).status_code == 500
