"""The parts of a vault's ``edda.toml`` that the Python services read: models, budgets and drivers.

The kernel reads ``[vault]`` itself. Every other table is optional, and every key has a default, so a missing key
never breaks a vault; an unknown key is an error, so a typo does not silently fall back to a default.
"""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError, field_validator, model_validator


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Endpoint(_Section):
    """An OpenAI-compatible chat-completions endpoint: a local model server, or a hosted one such as NVIDIA's.

    ``api_key_env`` names the environment variable that holds the endpoint's key; the key itself never goes in
    ``edda.toml``. An endpoint without one is called without authentication, as local servers expect.
    """

    base_url: str
    model: str
    api_key_env: str | None = None
    timeout_seconds: PositiveInt = 600


def _default_endpoints() -> dict[str, dict[str, Any]]:
    return {
        "local": {"base_url": "http://127.0.0.1:8080/v1", "model": "local"},
        "nvidia": {
            "base_url": "https://integrate.api.nvidia.com/v1",
            "model": "nvidia/nemotron-3-ultra-550b-a55b",
            "api_key_env": "NVIDIA_API_KEY",
            "timeout_seconds": 300,
        },
    }


class AnthropicLLM(_Section):
    """The Claude model each job uses when it is set to Anthropic. The API key comes from the environment, never here."""

    ingest: str = "claude-sonnet-5-5"
    query: str = "claude-sonnet-5-5"
    lint: str = "claude-opus-5-5"
    research: str = "claude-opus-5-5"
    summary: str = "claude-haiku-5-5"
    fallbacks: bool = True


class LLMConfig(_Section):
    """Which provider each job uses: the name of an endpoint, or ``anthropic``.

    ``local`` is the default: free, and nothing leaves the machine. ``nvidia`` is NVIDIA's hosted Nemotron and
    ``anthropic`` is Claude; both need the person's own key in the environment, and send sources off the machine.
    Endpoints given in ``edda.toml`` are merged over the built-in ``local`` and ``nvidia`` ones, key by key.
    """

    ingest: str = "local"
    query: str = "local"
    lint: str = "local"
    research: str = "local"
    summary: str = "local"
    endpoints: dict[str, Endpoint] = Field(default_factory=lambda: _endpoints({}))
    anthropic: AnthropicLLM = Field(default_factory=AnthropicLLM)

    @field_validator("endpoints", mode="before")
    @classmethod
    def _merge_defaults(cls, value: Any) -> Any:
        return _endpoints(value) if isinstance(value, dict) else value

    @model_validator(mode="after")
    def _known_providers(self) -> "LLMConfig":
        if "anthropic" in self.endpoints:
            raise ValueError("'anthropic' is a provider, not an endpoint name")
        for job in ("ingest", "query", "lint", "research", "summary"):
            name = getattr(self, job)
            if name != "anthropic" and name not in self.endpoints:
                known = ", ".join(sorted([*self.endpoints, "anthropic"]))
                raise ValueError(f"llm.{job} = {name!r} is not an endpoint or provider; known: {known}")
        return self


def _endpoints(given: dict[str, Any]) -> dict[str, Endpoint]:
    merged = _default_endpoints()
    for name, fields in given.items():
        merged[name] = {**merged.get(name, {}), **(fields if isinstance(fields, dict) else {})}
    return {name: Endpoint.model_validate(fields) for name, fields in merged.items()}


class Budgets(_Section):
    """Token budgets: how much context each job may pack into a prompt, and the longest reply it may ask for.

    The defaults fit a local model served with a 32k-token context (budget plus ``max_output_tokens`` stays under it).
    Claude's context is far larger: raise them for jobs set to Anthropic, which bills by the token.
    """

    ingest: PositiveInt = 20_000
    query: PositiveInt = 16_000
    lint: PositiveInt = 24_000
    research: PositiveInt = 24_000
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
