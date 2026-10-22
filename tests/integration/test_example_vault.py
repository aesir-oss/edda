from pathlib import Path

import pytest


@pytest.mark.integration
def test_example_vault_has_a_bundle_root(example_vault: Path) -> None:
    assert (example_vault / "wiki").is_dir()
