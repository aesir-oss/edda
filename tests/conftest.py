import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A home folder per test, so no test reads the real ~/.cache/edda (the local model's key, downloaded weights)."""
    path = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(path))
    return path


@pytest.fixture
def example_vault() -> Path:
    return REPO_ROOT / "examples" / "vault"


@pytest.fixture
def vault_copy(example_vault: Path, tmp_path: Path) -> Path:
    """The example vault copied to a temporary folder, so building its index writes nothing in the repository."""
    target = tmp_path / "vault"
    shutil.copytree(example_vault, target, ignore=shutil.ignore_patterns(".edda"))
    return target
