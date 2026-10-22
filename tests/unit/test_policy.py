from pathlib import Path

import pytest

from edda.policy import Network, PolicyError, load_policy


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("arxiv.org", True),
        ("ARXIV.org.", True),
        ("export.arxiv.org", False),
        ("en.wikipedia.org", True),
        ("de.wikipedia.org", True),
        ("wikipedia.org", False),
        ("evilwikipedia.org", False),
        ("example.com", False),
        (None, False),
        ("", False),
    ],
)
def test_allows_listed_hosts_and_subdomains_of_wildcards(host: str | None, expected: bool) -> None:
    assert Network(allow=["arxiv.org", "*.wikipedia.org"]).allows(host) is expected


def test_reads_policy_toml_and_defaults_to_no_network(tmp_path: Path) -> None:
    assert load_policy(tmp_path).network.allow == []
    (tmp_path / "policy.toml").write_text('[network]\nallow = ["arxiv.org"]\n[jobs]\nlint = "daily"\n')
    assert load_policy(tmp_path).network.allows("arxiv.org")


@pytest.mark.parametrize("text", ["[network\n", '[network]\nallow = "arxiv.org"\n', "[network]\ndeny = []\n"])
def test_rejects_malformed_policy(tmp_path: Path, text: str) -> None:
    (tmp_path / "policy.toml").write_text(text)
    with pytest.raises(PolicyError, match=r"policy\.toml"):
        load_policy(tmp_path)
