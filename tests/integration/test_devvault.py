from pathlib import Path

import pytest

from edda.devvault import DEV_VAULT, main, prepare

pytestmark = pytest.mark.integration


@pytest.fixture
def root(example_vault: Path, tmp_path: Path) -> Path:
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "vault").symlink_to(example_vault, target_is_directory=True)
    return tmp_path


def test_copies_once_and_keeps_the_copy_between_runs(root: Path) -> None:
    vault = prepare(root)
    assert vault == root / DEV_VAULT
    assert (vault / "wiki" / "attention.md").is_file()
    assert not (vault / ".edda").exists()
    (vault / "raw" / "mine.md").write_text("# Mine\n")
    assert prepare(root) == vault
    assert (vault / "raw" / "mine.md").exists()


def test_fresh_replaces_the_copy(root: Path) -> None:
    vault = prepare(root)
    (vault / "raw" / "mine.md").write_text("# Mine\n")
    assert main([str(root), "fresh"]) == 0
    assert not (vault / "raw" / "mine.md").exists()
    assert (vault / "wiki" / "attention.md").is_file()
    assert main([str(root)]) == 0


@pytest.mark.parametrize("args", [[], ["a", "wipe"], ["a", "b", "c"]])
def test_rejects_bad_arguments(args: list[str]) -> None:
    assert main(args) == 2
