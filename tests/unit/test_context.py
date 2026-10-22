from unittest.mock import MagicMock

import pytest

from edda.context import ContextOverflow, estimate_tokens, pack
from edda.kernel import Kernel
from edda.models import Citation, Page, Related


def _page(page_id: str, body: str, rev: int | None = 1) -> Page:
    citations = [Citation(id="s1", resource="raw:ab", cited=True)] if rev else []
    return Page(
        id=page_id, title=page_id.upper(), rev=rev, frontmatter={}, body=body, html="", citations=citations, issues=[]
    )


@pytest.fixture
def kernel() -> MagicMock:
    pages = {
        "index.md": _page("index.md", "- [A](/a.md)\n", rev=None),
        "a.md": _page("a.md", "short"),
        "big.md": _page("big.md", "x" * 4000),
        "b.md": _page("b.md", "also short", rev=None),
    }
    fake = MagicMock(spec=Kernel)
    fake.read_page.side_effect = lambda page_id: pages[page_id]
    fake.related.return_value = [
        Related(id="a.md", title="A", score=3, via="search"),
        Related(id="big.md", title="BIG", score=2, via="search"),
        Related(id="b.md", title="B", score=1, via="link"),
    ]
    return fake


def test_estimates_four_characters_per_token() -> None:
    assert (estimate_tokens(""), estimate_tokens("abc"), estimate_tokens("abcde")) == (0, 1, 2)


def test_packs_ranked_pages_that_fit_and_lists_the_rest(kernel: MagicMock) -> None:
    context = pack(kernel, instructions="Be exact.", source="new notes", budget=200)
    assert context.system.startswith("Be exact.\n\n# The wiki's index\n\n- [A](/a.md)")
    assert [p.id for p in context.pages] == ["a.md", "b.md"]
    assert context.omitted == ["big.md"]
    assert context.pages[0].text.startswith("## /a.md (revision 1): A.MD\nSources: s1 (raw:ab)")
    assert "Sources: none" in context.pages[1].text
    assert "(revision 0)" in context.pages[1].text
    assert context.tokens <= 200
    user = context.user_text()
    assert user.index("# Related pages") < user.index("# The new source\n\nnew notes")
    kernel.related.assert_called_once_with("new notes", 20)


def test_the_system_prompt_is_stable_across_sources(kernel: MagicMock) -> None:
    one = pack(kernel, instructions="Be exact.", source="first", budget=5000)
    two = pack(kernel, instructions="Be exact.", source="second, different", budget=5000)
    assert one.system == two.system


def test_refuses_rather_than_truncating(kernel: MagicMock) -> None:
    with pytest.raises(ContextOverflow, match="over the budget of 10"):
        pack(kernel, instructions="Be exact.", source="s" * 400, budget=10)
    kernel.related.return_value = []
    assert pack(kernel, instructions="i", source="s", budget=100).user_text().count("(no related pages)") == 1
