"""The owner's rules in ``<vault>/policy.toml``. Until edda-policy (#37) gives verdicts, the services read the file."""

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class PolicyError(ValueError):
    """``policy.toml`` is not valid TOML, or does not match the schema."""


class Network(BaseModel):
    """Which hosts the services may fetch from. Nothing is allowed unless listed."""

    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)

    def allows(self, host: str | None) -> bool:
        """True when ``host`` is listed exactly, or matches a ``*.example.org`` entry as a subdomain."""
        if not host:
            return False
        host = host.lower().rstrip(".")
        for entry in (e.lower().rstrip(".") for e in self.allow):
            if entry.startswith("*.") and host.endswith(entry[1:]) and host != entry[2:]:
                return True
            if host == entry:
                return True
        return False


class Policy(BaseModel):
    """Everything in ``policy.toml``."""

    model_config = ConfigDict(extra="allow")

    network: Network = Field(default_factory=Network)


def load_policy(vault: Path) -> Policy:
    """Reads ``<vault>/policy.toml``; a vault without one allows no network access.

    Raises:
        PolicyError: The file is not valid TOML or does not match the schema.
    """
    path = vault / "policy.toml"
    if not path.exists():
        return Policy()
    try:
        return Policy.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise PolicyError(f"{path}: {exc}") from exc
