from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from edda.api import create_app
from edda.kernel import InvalidRequestError, Kernel, NotFoundError
from edda.models import (
    BrokenLink,
    Citation,
    GraphIssues,
    JournalEntry,
    Links,
    OutLink,
    Page,
    PageSummary,
    SearchHit,
    Source,
)


@pytest.fixture
def kernel() -> MagicMock:
    fake = MagicMock(spec=Kernel)
    fake.title = "Test vault"
    fake.pages.return_value = [
        PageSummary(id="a.md", title="A", type="Concept", tags=["ml"], status="stable"),
        PageSummary(id="b.md", title="B", type="Paper", tags=[], status="draft"),
    ]
    return fake


@pytest.fixture
def client(kernel: MagicMock) -> TestClient:
    return TestClient(create_app(kernel=kernel))


def test_health_and_vault(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/vault").json() == {"title": "Test vault", "okf_version": "0.2", "pages": 2}


@pytest.mark.parametrize(
    ("query", "expected"),
    [("", ["a.md", "b.md"]), ("?tag=ml", ["a.md"]), ("?type=Paper", ["b.md"]), ("?tag=ml&type=Paper", [])],
)
def test_lists_and_filters_pages(client: TestClient, query: str, expected: list[str]) -> None:
    assert [p["id"] for p in client.get(f"/api/pages{query}").json()] == expected


def test_reads_a_nested_page(client: TestClient, kernel: MagicMock) -> None:
    kernel.read_page.return_value = Page(
        id="papers/x.md",
        title="X",
        rev=3,
        frontmatter={"type": "Paper"},
        body="b",
        html="<p>b</p>",
        citations=[Citation(id="s", resource="raw:ab", raw="ab" * 32, cited=True)],
        issues=[],
    )
    body = client.get("/api/pages/papers/x.md").json()
    kernel.read_page.assert_called_once_with("papers/x.md")
    assert body["rev"] == 3
    assert body["citations"][0]["raw"] == "ab" * 32


def test_maps_kernel_errors_to_status_codes(client: TestClient, kernel: MagicMock) -> None:
    kernel.read_page.side_effect = NotFoundError("no page gone.md")
    response = client.get("/api/pages/gone.md")
    assert (response.status_code, response.json()) == (404, {"detail": "no page gone.md"})
    kernel.links.side_effect = InvalidRequestError("bad id")
    assert client.get("/api/links/x.txt").status_code == 400


def test_search_validates_its_parameters(client: TestClient, kernel: MagicMock) -> None:
    kernel.search.return_value = [SearchHit(id="a.md", title="A", description="", score=1.5, snippet="<b>a</b>")]
    assert client.get("/api/search", params={"q": "a", "k": 3}).json()[0]["score"] == 1.5
    kernel.search.assert_called_once_with("a", 3)
    assert client.get("/api/search", params={"q": ""}).status_code == 422
    assert client.get("/api/search", params={"q": "a", "k": 0}).status_code == 422


def test_sources_links_history_and_graph(client: TestClient, kernel: MagicMock) -> None:
    kernel.read_source.return_value = Source(hash="ab", text="line\n")
    assert client.get("/api/sources/ab", params={"start": 2, "end": 4}).json()["text"] == "line\n"
    kernel.read_source.assert_called_once_with("ab", 2, 4)

    kernel.links.return_value = Links(outgoing=[OutLink(dest="https://x.y")], backlinks=["b.md"])
    assert client.get("/api/links/a.md").json() == {
        "outgoing": [{"dest": "https://x.y", "target": None}],
        "backlinks": ["b.md"],
    }

    kernel.history.return_value = [
        JournalEntry(seq=1, at="2026-10-21T00:00:00Z", actor="owner", action="edit", summary="s", prev="0", hash="h")
    ]
    assert client.get("/api/history/a.md").json()[0]["seq"] == 1

    kernel.graph_issues.return_value = GraphIssues(
        orphans=["b.md"], broken_links=[BrokenLink.model_validate({"from": "a.md", "dest": "/c.md"})]
    )
    assert client.get("/api/graph/issues").json()["broken_links"] == [{"from": "a.md", "dest": "/c.md"}]

    assert client.post("/api/index/refresh").status_code == 204
    kernel.refresh_index.assert_called_once_with()


def test_without_a_vault_the_api_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDDA_VAULT", raising=False)
    client = TestClient(create_app())
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/pages").status_code == 503
