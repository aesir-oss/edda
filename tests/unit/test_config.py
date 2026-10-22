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
    assert config.budgets.ingest == 20_000
    assert config.drivers.web.timeout_seconds == 20


@pytest.mark.parametrize(
    "text",
    [
        "[llm]\ningets = 'local'\n",
        "[llm]\ningest = 'openai'\n",
        "[llm.endpoints.anthropic]\nbase_url = 'x'\nmodel = 'y'\n",
        "[llm.endpoints.mine]\nmodel = 'no base_url'\n",
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


def test_endpoints_merge_over_the_built_in_ones(tmp_path: Path) -> None:
    (tmp_path / "edda.toml").write_text(
        "[llm]\nquery = 'ollama'\n"
        "[llm.endpoints.local]\nmodel = 'Qwen3-14B-Q4_K_M.gguf'\n"
        "[llm.endpoints.ollama]\nbase_url = 'http://localhost:11434/v1'\nmodel = 'qwen3:14b'\n"
    )
    llm = load_config(tmp_path).llm
    assert llm.endpoints["local"].base_url == "http://127.0.0.1:8080/v1"
    assert llm.endpoints["local"].model == "Qwen3-14B-Q4_K_M.gguf"
    assert llm.endpoints["nvidia"].api_key_env == "NVIDIA_API_KEY"
    assert (llm.query, llm.endpoints["ollama"].api_key_env) == ("ollama", None)
