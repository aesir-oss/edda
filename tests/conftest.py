import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def example_vault() -> Path:
    return REPO_ROOT / "examples" / "vault"


@pytest.fixture
def vault_copy(example_vault: Path, tmp_path: Path) -> Path:
    """The example vault copied to a temporary folder, so building its index writes nothing in the repository."""
    target = tmp_path / "vault"
    shutil.copytree(example_vault, target, ignore=shutil.ignore_patterns(".edda"))
    return target
