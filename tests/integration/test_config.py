from pathlib import Path

import pytest

from edda.config import EddaConfig, load_config

pytestmark = pytest.mark.integration


def test_the_example_vault_spells_out_the_defaults(example_vault: Path) -> None:
    config = load_config(example_vault)
    assert config.vault["title"] == "Edda example vault"
    assert config.model_dump(exclude={"vault"}) == EddaConfig().model_dump(exclude={"vault"})
