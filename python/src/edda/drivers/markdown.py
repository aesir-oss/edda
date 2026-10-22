"""The Markdown importer: stores a Markdown file unedited as a raw source."""

import re
from pathlib import PurePath

import structlog

from edda.drivers.errors import DriverError
from edda.kernel import Kernel
from edda.models import AddedSource

log = structlog.get_logger("edda.drivers.markdown")

_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n(?:---|\.\.\.)\r?\n", re.DOTALL)
_HEADING = re.compile(r"^#[ \t]+(.+?)[ \t#]*$", re.MULTILINE)


def markdown_title(text: str) -> str | None:
    """The first level-one ATX heading, ignoring a frontmatter block; ``None`` when there is none."""
    match = _HEADING.search(_FRONTMATTER.sub("", text, count=1))
    return match.group(1).strip() if match else None


def import_markdown(
    kernel: Kernel,
    data: bytes,
    *,
    filename: str | None = None,
    url: str | None = None,
    title: str | None = None,
    by: str = "owner",
) -> AddedSource:
    """Stores a Markdown file in ``raw/`` exactly as given, with its title in the metadata.

    The title is ``title`` when given, otherwise the file's first ``# `` heading, otherwise the file name without
    its extension.

    Raises:
        DriverError: The bytes are empty or not UTF-8.
    """
    if not data.strip():
        raise DriverError("the file is empty")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DriverError(f"not UTF-8 text: {exc}") from exc
    name = PurePath(filename).stem if filename else None
    resolved = title or markdown_title(text) or name
    added = kernel.add_source(data, extension="md", title=resolved, url=url, driver="markdown", by=by)
    log.info("source_imported", driver="markdown", hash=added.source.hash, added=added.added, title=resolved)
    return added
