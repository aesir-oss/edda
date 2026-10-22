from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def example_vault() -> Path:
    return REPO_ROOT / "examples" / "vault"
