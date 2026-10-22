"""The Anthropic provider, used only when a job selects it and the person has supplied their own credentials.

The key is read by the SDK from the environment (``ANTHROPIC_API_KEY``, ``ANTHROPIC_AUTH_TOKEN``) or an ``ant auth
login`` profile. Edda never stores it in the vault, ``edda.toml`` or logs.
"""

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import anthropic
import structlog

from edda.config import AnthropicLLM
from edda.llm.errors import LLMError, ProviderNotConfigured
from edda.llm.types import AssistantTurn, ChatResult, Stop, Tool, ToolCall, ToolTurn, Turn, Usage, UserTurn

log = structlog.get_logger("edda.llm.claude")

_CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID")
_STOPS: dict[str, Stop] = {"end_turn": "end", "tool_use": "tool_use", "max_tokens": "max_tokens", "refusal": "refusal"}
# Models that accept the server-side refusal fallback; on a decline the API re-runs the request on a fallback model.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def has_credentials(env: Mapping[str, str] | None = None, home: Path | None = None) -> bool:
    """True when the SDK can find the person's credentials: an environment variable or an ``ant auth login`` profile."""
    env = os.environ if env is None else env
    if any(env.get(name) for name in _CREDENTIAL_VARS):
        return True
    return ((home or Path.home()) / ".config" / "anthropic").is_dir()


class ClaudeProvider:
    """Calls Claude through the Anthropic SDK. ``client`` is injectable so tests can replay recorded responses."""

    name = "anthropic"

    def __init__(self, config: AnthropicLLM, client: Any = None, retries: int = 2) -> None:
        """Refuses to start without credentials rather than failing on the first call.

        Raises:
            ProviderNotConfigured: No API key or profile is available.
        """
        if client is None and not has_credentials():
            raise ProviderNotConfigured(
                "this job is set to use Anthropic, which needs your own API key: set ANTHROPIC_API_KEY (or run "
                '`ant auth login`), or set the job back to "local" in edda.toml'
            )
        self.config = config
        self.client = client or anthropic.Anthropic(max_retries=retries)

    def chat(
        self, *, model: str, system: str, turns: Sequence[Turn], tools: Sequence[Tool], max_tokens: int
    ) -> ChatResult:
        """Sends one Messages API request. The system prompt and tools form a stable prefix and are cached."""
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": _messages(turns),
        }
        if tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
            ]
        if self.config.fallbacks and model in _FALLBACK_MODELS:
            params["betas"] = [_FALLBACK_BETA]
            params["fallbacks"] = "default"
        try:
            response = self.client.beta.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise ProviderNotConfigured(f"Anthropic rejected the credentials: {exc.message}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"could not reach the Anthropic API: {exc}") from exc

        native = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input)) for b in response.content if b.type == "tool_use"
        ]
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            log.warning("anthropic_refusal", model=response.model, category=category)
        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
        )
        return ChatResult(
            turn=AssistantTurn(text=text, tool_calls=calls, native=native),
            stop=_STOPS.get(response.stop_reason or "end_turn", "end"),
            usage=usage,
            provider="anthropic",
            model=response.model,
        )


def _messages(turns: Sequence[Turn]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for turn in turns:
        if isinstance(turn, UserTurn):
            out.append({"role": "user", "content": turn.text})
        elif isinstance(turn, ToolTurn):
            results = [
                {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content, "is_error": r.is_error}
                for r in turn.results
            ]
            out.append({"role": "user", "content": results})
        elif turn.native is not None:
            # Replayed unchanged: thinking blocks must come back exactly as they were returned.
            out.append({"role": "assistant", "content": turn.native})
        else:
            blocks: list[dict[str, Any]] = [{"type": "text", "text": turn.text}] if turn.text else []
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in turn.tool_calls]
            out.append({"role": "assistant", "content": blocks})
    return out
