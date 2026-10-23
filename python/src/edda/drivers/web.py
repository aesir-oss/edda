"""The web page importer: fetches a URL the policy allows and stores its readable text as Markdown."""

import re
from urllib.parse import urljoin, urlsplit

import httpx
import structlog
import trafilatura

from edda.config import WebDriver
from edda.drivers.errors import DriverError, FetchFailed, NotAllowed
from edda.drivers.markdown import markdown_title
from edda.kernel import Kernel
from edda.models import AddedSource
from edda.policy import Network

log = structlog.get_logger("edda.drivers.web")

MAX_REDIRECTS = 5
_TEXT = {"text/markdown", "text/plain", "text/x-markdown"}
_HTML = {"text/html", "application/xhtml+xml"}
_LINE_START = re.compile(r"\s*(?:[-*+]\s|\d+[.)]\s|#|>|\|)")


def _class(name: str) -> str:
    return f"//*[contains(concat(' ', normalize-space(@class), ' '), ' {name} ')]"


# Page furniture trafilatura can mistake for content: navigation and hidden controls on any site, and MediaWiki's
# edit links, maintenance banners, navboxes and reference markers.
PRUNE = [
    "//nav",
    "//*[@role='navigation']",
    "//*[@aria-hidden='true']",
    *map(
        _class,
        ["vector-pinnable-header", "mw-jump-link", "mw-editsection", "metadata", "ambox", "navbox", "noprint"],
    ),
    _class("reference"),
    _class("mw-cite-backlink"),
]


def import_url(
    kernel: Kernel,
    url: str,
    *,
    network: Network,
    config: WebDriver,
    client: httpx.Client | None = None,
    title: str | None = None,
    by: str = "owner",
) -> AddedSource:
    """Fetches ``url`` and stores its text in ``raw/`` with the final URL in the metadata, so pages can cite it.

    HTML is reduced to its main text as Markdown without links (citations point at the page, and links cost the model
    context), titled by its first heading, else the page's title. Markdown and plain text are stored as served. Every redirect is
    checked against the policy before it is followed.

    Raises:
        NotAllowed: The URL, or a redirect, is not http(s) on a host ``[network] allow`` lists.
        FetchFailed: The server could not be reached, answered with an error, or sent too much.
        DriverError: The response is not a kind of text this importer reads, or has no readable text.
    """
    owned = client is None
    http = client or httpx.Client(timeout=config.timeout_seconds)
    try:
        final, content_type, data = _fetch(http, url, network, config)
    finally:
        if owned:
            http.close()
    if content_type in _HTML:
        text, found = _readable(data, final)
        heading = markdown_title(text)
        resolved = title or heading or found
        body = text if heading or not resolved else f"# {resolved}\n\n{text}"
        stored = body.rstrip() + "\n"
    elif content_type in _TEXT:
        stored = _decode(data)
        resolved = title
    else:
        raise DriverError(f"{final} is {content_type or 'of no stated type'}; documents are imported as files")
    added = kernel.add_source(stored.encode("utf-8"), extension="md", title=resolved, url=final, driver="web", by=by)
    log.info("source_imported", driver="web", url=final, hash=added.source.hash, added=added.added, title=resolved)
    return added


def _fetch(http: httpx.Client, url: str, network: Network, config: WebDriver) -> tuple[str, str, bytes]:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _check(current, network)
        try:
            with http.stream("GET", current, headers={"User-Agent": config.user_agent}, follow_redirects=False) as r:
                if r.is_redirect and "location" in r.headers:
                    current = urljoin(current, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise FetchFailed(f"{current} answered HTTP {r.status_code}")
                declared = int(r.headers.get("content-length") or 0)
                if declared > config.max_bytes:
                    raise FetchFailed(f"{current} is {declared} bytes; the limit is {config.max_bytes}")
                chunks: list[bytes] = []
                size = 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > config.max_bytes:
                        raise FetchFailed(f"{current} is over the {config.max_bytes}-byte limit")
                    chunks.append(chunk)
                content_type = r.headers.get("content-type", "").split(";")[0].strip().lower()
                return current, content_type, b"".join(chunks)
        except httpx.HTTPError as exc:
            raise FetchFailed(f"could not fetch {current}: {exc}") from exc
    raise FetchFailed(f"{url} redirected more than {MAX_REDIRECTS} times")


def _check(url: str, network: Network) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise NotAllowed(f"only http and https URLs can be fetched, not {url}")
    if not network.allows(parts.hostname):
        raise NotAllowed(f"{parts.hostname} is not in [network] allow in policy.toml")


def _decode(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    if not text.strip():
        raise DriverError("the page is empty")
    return text


def _readable(data: bytes, url: str) -> tuple[str, str | None]:
    html = _decode(data)
    text = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_tables=True,
        include_links=False,
        include_comments=False,
        prune_xpath=PRUNE,
    )
    if not text or not text.strip():
        raise DriverError(f"found no readable text at {url}")
    metadata = trafilatura.extract_metadata(html, default_url=url)
    return _unwrap(_once(text)), (metadata.title if metadata and metadata.title else None)


def _once(text: str) -> str:
    """On short pages trafilatura's fallback can return the body twice, after the heading; keep one copy."""
    blocks = text.split("\n\n")
    head = 1 if blocks[0].startswith("# ") else 0
    body = blocks[head:]
    half = len(body) // 2
    if half and len(body) % 2 == 0 and body[:half] == body[half:]:
        return "\n\n".join(blocks[:head] + body[:half])
    return text


def _unwrap(text: str) -> str:
    """Joins the line breaks HTML puts inside a paragraph (only spaces there); headings, lists, tables and code keep theirs."""
    blocks = []
    for block in text.split("\n\n"):
        if block.lstrip().startswith(("```", "|")):
            blocks.append(block)
            continue
        lines: list[str] = []
        for line in block.split("\n"):
            if lines and line.strip() and not _LINE_START.match(line) and not lines[-1].lstrip().startswith("#"):
                lines[-1] = f"{lines[-1].rstrip()} {line.strip()}"
            else:
                lines.append(line)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
