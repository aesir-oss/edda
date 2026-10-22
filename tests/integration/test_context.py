from pathlib import Path

import pytest

from edda.context import pack
from edda.kernel import Kernel

pytestmark = pytest.mark.integration

SOURCE = "Masked self-attention in the decoder keeps each position from attending to later positions."


def test_ranks_by_similarity_then_links(vault_copy: Path) -> None:
    related = Kernel(vault_copy).related(SOURCE, 6)
    assert [(r.id, r.via) for r in related[:3]] == [
        ("transformer.md", "search"),
        ("attention.md", "search"),
        ("papers/attention-is-all-you-need.md", "link"),
    ]


def test_packs_the_example_vault_for_a_new_source(vault_copy: Path) -> None:
    kernel = Kernel(vault_copy)
    context = pack(kernel, instructions="Propose edits with citations.", source=SOURCE, budget=4000)
    assert [p.id for p in context.pages][:2] == ["transformer.md", "attention.md"]
    assert "/attention.md" in context.system
    assert "(revision 1)" in context.pages[0].text
    small = pack(kernel, instructions="Propose edits.", source=SOURCE, budget=context.tokens - 50)
    assert small.omitted
