from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from edda.api import create_app
from edda.api.app import get_today, is_stale
from edda.kernel import InvalidRequestError, Kernel, NotFoundError
from edda.models import (
    AddedSource,
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
    SourceSummary,
)


@pytest.fixture
def kernel() -> MagicMock:
    fake = MagicMock(spec=Kernel)
    fake.title = "Test vault"
    fake.pages.return_value = [
        PageSummary(id="a.md", title="A", type="Concept", tags=["ml"], status="stable", sources=["ab12" + "0" * 60]),
        PageSummary(id="b.md", title="B", type="Paper", tags=[], status="draft", stale_after="2026-01-01"),
    ]
    return fake


@pytest.fixture
def client(kernel: MagicMock) -> TestClient:
    app = create_app(kernel=kernel)
    app.dependency_overrides[get_today] = lambda: date(2026, 6, 1)
    return TestClient(app)


def test_health_and_vault(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/vault").json() == {"title": "Test vault", "okf_version": "0.2", "pages": 2}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", ["a.md", "b.md"]),
        ("?tag=ml", ["a.md"]),
        ("?type=Paper", ["b.md"]),
        ("?tag=ml&type=Paper", []),
        ("?source=AB12", ["a.md"]),
        ("?source=ffff", []),
        ("?status=draft", ["b.md"]),
        ("?stale=true", ["b.md"]),
        ("?stale=false", ["a.md"]),
    ],
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
    kernel.read_source.assert_called_once_with("ab", 2, 4, None)

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


def test_marks_stale_pages_and_lists_sources(client: TestClient, kernel: MagicMock) -> None:
    pages = client.get("/api/pages").json()
    assert [p["stale"] for p in pages] == [False, True]
    kernel.sources.return_value = [SourceSummary(hash="ab12", title="Notes", driver="markdown")]
    assert client.get("/api/sources").json() == [
        {
            "hash": "ab12",
            "title": "Notes",
            "url": None,
            "added": None,
            "driver": "markdown",
            "derived_from": None,
            "method": None,
        }
    ]


@pytest.mark.parametrize(
    ("stale_after", "expected"),
    [
        (None, False),
        ("", False),
        ("2026-05-31", True),
        ("2026-06-01", False),
        ("2026-05-31T23:00:00Z", True),
        ("next spring", False),
    ],
)
def test_is_stale_compares_dates(stale_after: str | None, expected: bool) -> None:
    assert is_stale(stale_after, date(2026, 6, 1)) is expected


def test_get_today_is_a_date() -> None:
    assert isinstance(get_today(), date)


def test_imports_markdown_from_the_request_body(client: TestClient, kernel: MagicMock) -> None:
    kernel.add_source.return_value = AddedSource(source=SourceSummary(hash="ab", title="Notes"), added=True)
    response = client.post("/api/sources/markdown", params={"filename": "n.md"}, content=b"# Notes\n")
    assert (response.status_code, response.json()["source"]["title"]) == (201, "Notes")
    assert kernel.add_source.call_args.args == (b"# Notes\n",)
    kernel.add_source.return_value = AddedSource(source=SourceSummary(hash="ab"), added=False)
    assert client.post("/api/sources/markdown", content=b"# Notes\n").status_code == 200
    assert client.post("/api/sources/markdown", content=b"").status_code == 400


def test_refuses_oversized_uploads(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("edda.api.app.MAX_UPLOAD_BYTES", 4)
    assert client.post("/api/sources/markdown", content=b"# too long").status_code == 413
