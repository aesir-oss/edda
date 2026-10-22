"""The parts of a vault's ``edda.toml`` that the Python services read: models, budgets and drivers.

The kernel reads ``[vault]`` itself. Every other table is optional, and every key has a default, so a missing key
never breaks a vault; an unknown key is an error, so a typo does not silently fall back to a default.
"""

import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalLLM(_Section):
    """A local model server with an OpenAI-compatible chat-completions API (llama.cpp's llama-server by default)."""

    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "local"
    timeout_seconds: PositiveInt = 600


class AnthropicLLM(_Section):
    """The Claude model each job uses when it is set to Anthropic. The API key comes from the environment, never here."""

    ingest: str = "claude-sonnet-5-5"
    query: str = "claude-sonnet-5-5"
    lint: str = "claude-opus-5-5"
    research: str = "claude-opus-5-5"
    summary: str = "claude-haiku-5-5"
    fallbacks: bool = True


class LLMConfig(_Section):
    """Which provider each job uses.

    ``local`` is the default: free, and nothing leaves the machine. ``anthropic`` needs the person's own API key, and
    they pay for it.
    """

    ingest: Literal["local", "anthropic"] = "local"
    query: Literal["local", "anthropic"] = "local"
    lint: Literal["local", "anthropic"] = "local"
    research: Literal["local", "anthropic"] = "local"
    summary: Literal["local", "anthropic"] = "local"
    local: LocalLLM = Field(default_factory=LocalLLM)
    anthropic: AnthropicLLM = Field(default_factory=AnthropicLLM)


class Budgets(_Section):
    """Token budgets: how much context each job may pack into a prompt, and the longest reply it may ask for."""

    ingest: PositiveInt = 60_000
    query: PositiveInt = 40_000
    lint: PositiveInt = 80_000
    research: PositiveInt = 80_000
    max_output_tokens: PositiveInt = 8_000


class WebDriver(_Section):
    """Fetching web pages. Which hosts may be fetched is policy, in ``policy.toml``."""

    timeout_seconds: PositiveInt = 20
    max_bytes: PositiveInt = 5_000_000
    user_agent: str = "Edda/0.1 (+https://github.com/aesir-oss/edda)"


class PdfDriver(_Section):
    """Extracting text from PDFs."""

    library: str = "pypdf"


class Drivers(_Section):
    """Importer settings."""

    web: WebDriver = Field(default_factory=WebDriver)
    pdf: PdfDriver = Field(default_factory=PdfDriver)


class EddaConfig(_Section):
    """Everything in ``edda.toml`` outside ``[vault]``."""

    vault: dict[str, Any] = Field(default_factory=dict)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    budgets: Budgets = Field(default_factory=Budgets)
    drivers: Drivers = Field(default_factory=Drivers)


class ConfigError(ValueError):
    """``edda.toml`` is not valid TOML, or has a key or value the services do not accept."""


def load_config(vault: Path) -> EddaConfig:
    """Reads ``<vault>/edda.toml``; a vault without one gets the defaults.

    Raises:
        ConfigError: The file is not valid TOML or does not match the schema.
    """
    path = vault / "edda.toml"
    if not path.exists():
        return EddaConfig()
    try:
        return EddaConfig.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
