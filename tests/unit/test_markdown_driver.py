from unittest.mock import MagicMock

import pytest

from edda.drivers import DriverError, import_markdown
from edda.drivers.markdown import markdown_title
from edda.kernel import Kernel
from edda.models import AddedSource, SourceSummary


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# Notes on BM25\n\nbody", "Notes on BM25"),
        ("intro\n\n## Sub\n\n# Late title ##\n", "Late title"),
        ("---\ntitle: x\n---\n# Real\n", "Real"),
        ("---\n# not a heading, frontmatter\n---\nno heading\n", None),
        ("#hashtag\n", None),
    ],
)
def test_reads_the_first_level_one_heading(text: str, expected: str | None) -> None:
    assert markdown_title(text) == expected


@pytest.fixture
def kernel() -> MagicMock:
    fake = MagicMock(spec=Kernel)
    fake.add_source.return_value = AddedSource(source=SourceSummary(hash="ab"), added=True)
    return fake


def test_titles_come_from_the_argument_the_heading_or_the_file_name(kernel: MagicMock) -> None:
    import_markdown(kernel, b"# Heading\n", filename="notes.md", title="Given")
    import_markdown(kernel, b"# Heading\n", filename="notes.md")
    import_markdown(kernel, b"no heading\n", filename="dir/notes.md", url="https://x.example")
    titles = [call.kwargs["title"] for call in kernel.add_source.call_args_list]
    assert titles == ["Given", "Heading", "notes"]
    last = kernel.add_source.call_args_list[-1]
    assert last.args == (b"no heading\n",)
    assert last.kwargs == {
        "extension": "md",
        "title": "notes",
        "url": "https://x.example",
        "driver": "markdown",
        "by": "owner",
    }


@pytest.mark.parametrize("data", [b"", b"  \n", b"\xff\xfe not utf-8"])
def test_refuses_empty_and_binary_files(kernel: MagicMock, data: bytes) -> None:
    with pytest.raises(DriverError):
        import_markdown(kernel, data)
    kernel.add_source.assert_not_called()
