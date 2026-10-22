from pathlib import Path

import pytest

from edda.config import ConfigError, EddaConfig, load_config


def test_a_vault_without_edda_toml_gets_the_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path) == EddaConfig()
    assert EddaConfig().llm.ingest == "local"
    assert EddaConfig().llm.anthropic.lint == "claude-opus-5-5"


def test_missing_keys_take_defaults(tmp_path: Path) -> None:
    (tmp_path / "edda.toml").write_text(
        '[vault]\ntitle = "x"\n[llm]\ningest = "anthropic"\n[llm.anthropic]\ningest = "claude-haiku-5-5"\n'
    )
    config = load_config(tmp_path)
    assert config.vault["title"] == "x"
    assert (config.llm.ingest, config.llm.anthropic.ingest) == ("anthropic", "claude-haiku-5-5")
    assert (config.llm.query, config.llm.anthropic.query) == ("local", "claude-sonnet-5-5")
    assert config.budgets.ingest == 60_000
    assert config.drivers.web.timeout_seconds == 20


@pytest.mark.parametrize(
    "text",
    [
        "[llm]\ningets = 'local'\n",
        "[llm]\ningest = 'openai'\n",
        "[budgets]\ningest = 0\n",
        "[drivers.web]\ntimeout_seconds = 'soon'\n",
        "[unknown]\n",
        "not = [toml",
    ],
)
def test_rejects_typos_and_bad_values(tmp_path: Path, text: str) -> None:
    (tmp_path / "edda.toml").write_text(text)
    with pytest.raises(ConfigError, match=r"edda\.toml"):
        load_config(tmp_path)
