from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from edda.config import WebDriver
from edda.drivers import DriverError, FetchFailed, NotAllowed, import_url
from edda.drivers.web import _once
from edda.kernel import Kernel
from edda.models import AddedSource, SourceSummary
from edda.policy import Network

HTML = (Path(__file__).parents[1] / "fixtures" / "bm25.html").read_bytes()
NETWORK = Network(allow=["en.wikipedia.org", "*.example.org"])
CONFIG = WebDriver(max_bytes=100_000)


@pytest.fixture
def kernel() -> MagicMock:
    fake = MagicMock(spec=Kernel)
    fake.add_source.side_effect = lambda data, **meta: AddedSource(
        source=SourceSummary(hash="ab" * 32, title=meta["title"], url=meta["url"], driver=meta["driver"]), added=True
    )
    return fake


def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def stored(kernel: MagicMock) -> str:
    data: bytes = kernel.add_source.call_args.args[0]
    return data.decode("utf-8")


def test_stores_the_readable_text_of_an_html_page(kernel: MagicMock) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=HTML, headers={"content-type": "text/html; charset=utf-8"})

    url = "https://en.wikipedia.org/wiki/Okapi_BM25"
    added = import_url(kernel, url, network=NETWORK, config=CONFIG, client=client(handler))
    assert (added.source.title, added.source.url, added.source.driver) == ("Okapi BM25", url, "web")
    text = stored(kernel)
    assert text.startswith("# Okapi BM25\n\nIn information retrieval")
    assert "| k1 | 1.2 |" in text
    assert text.count("bag-of-words") == 1
    assert "Cookie statement" not in text and "Random article" not in text
    assert kernel.add_source.call_args.kwargs["extension"] == "md"
    assert seen[0].headers["user-agent"].startswith("Edda/")


def test_adds_a_heading_when_the_page_text_has_none(kernel: MagicMock) -> None:
    html = HTML.replace(b"<h1>Okapi BM25</h1>", b"")
    handler = lambda _: httpx.Response(200, content=html, headers={"content-type": "text/html"})  # noqa: E731
    import_url(
        kernel, "https://en.wikipedia.org/x", network=NETWORK, config=CONFIG, client=client(handler), title="BM25"
    )
    assert stored(kernel).startswith("# BM25\n\n")
    assert kernel.add_source.call_args.kwargs["title"] == "BM25"


def test_stores_markdown_as_served_and_follows_allowed_redirects(kernel: MagicMock) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "https://docs.example.org/new.md"})
        return httpx.Response(200, text="# Notes\n\nPlain words.\n", headers={"content-type": "text/markdown"})

    added = import_url(kernel, "https://www.example.org/old", network=NETWORK, config=CONFIG, client=client(handler))
    assert added.source.url == "https://docs.example.org/new.md"
    assert stored(kernel) == "# Notes\n\nPlain words.\n"


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://en.wikipedia.org/x", "only http and https"),
        ("file:///etc/passwd", "only http and https"),
        ("https://example.com/", "example.com is not in"),
        ("https://example.org/", "example.org is not in"),
    ],
)
def test_refuses_what_the_policy_does_not_allow(kernel: MagicMock, url: str, message: str) -> None:
    with pytest.raises(NotAllowed, match=message):
        import_url(kernel, url, network=NETWORK, config=CONFIG)
    kernel.add_source.assert_not_called()


def test_checks_every_redirect_against_the_policy(kernel: MagicMock) -> None:
    handler = lambda _: httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})  # noqa: E731
    with pytest.raises(NotAllowed, match=r"169\.254\.169\.254"):
        import_url(kernel, "https://a.example.org/", network=NETWORK, config=CONFIG, client=client(handler))


def test_gives_up_after_too_many_redirects(kernel: MagicMock) -> None:
    handler = lambda r: httpx.Response(302, headers={"location": f"{r.url}x"})  # noqa: E731
    with pytest.raises(FetchFailed, match="more than 5 times"):
        import_url(kernel, "https://a.example.org/", network=NETWORK, config=CONFIG, client=client(handler))


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        (httpx.Response(404, text="gone"), FetchFailed, "HTTP 404"),
        (httpx.Response(200, content=b"x" * 10, headers={"content-length": "999999"}), FetchFailed, "999999 bytes"),
        (httpx.Response(200, content=b"x" * 200_000, headers={"content-type": "text/plain"}), FetchFailed, "limit"),
        (httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}), DriverError, "pdf"),
        (httpx.Response(200, content=b"x"), DriverError, "no stated type"),
        (httpx.Response(200, content=b"  \n", headers={"content-type": "text/plain"}), DriverError, "empty"),
        (
            httpx.Response(200, content=b"<html><body></body></html>", headers={"content-type": "text/html"}),
            DriverError,
            "no readable",
        ),
    ],
)
def test_reports_responses_it_cannot_use(
    kernel: MagicMock, response: httpx.Response, error: type[DriverError], message: str
) -> None:
    with pytest.raises(error, match=message):
        import_url(kernel, "https://a.example.org/", network=NETWORK, config=CONFIG, client=client(lambda _: response))
    kernel.add_source.assert_not_called()


def test_reports_unreachable_servers(kernel: MagicMock) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with pytest.raises(FetchFailed, match="no route to host"):
        import_url(kernel, "https://a.example.org/", network=NETWORK, config=CONFIG, client=client(handler))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# T\n\nA\n\nB\n\nA\n\nB", "# T\n\nA\n\nB"),
        ("A\n\nA", "A"),
        ("# T\n\nA\n\nB", "# T\n\nA\n\nB"),
        ("# T", "# T"),
    ],
)
def test_keeps_one_copy_of_a_body_extracted_twice(text: str, expected: str) -> None:
    assert _once(text) == expected


def test_prefers_the_pages_own_heading_to_its_html_title(kernel: MagicMock) -> None:
    html = HTML.replace(b"<title>Okapi BM25 - Wikipedia</title>", b"<title>Site name | Okapi BM25 page</title>")
    handler = lambda _: httpx.Response(200, content=html, headers={"content-type": "text/html"})  # noqa: E731
    added = import_url(kernel, "https://en.wikipedia.org/x", network=NETWORK, config=CONFIG, client=client(handler))
    assert added.source.title == "Okapi BM25"
