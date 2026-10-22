"""OpenAI-compatible chat-completions endpoints with tool calling: local model servers and hosted ones.

The ``local`` endpoint is llama.cpp's ``llama-server`` by default; Ollama, mlx-lm, vLLM and LM Studio serve the same
API. Hosted endpoints such as NVIDIA's (Nemotron) take a key, read from the environment variable the endpoint names.
"""

import json
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from edda.config import Endpoint
from edda.llm.errors import LLMError, ProviderNotConfigured
from edda.llm.types import AssistantTurn, ChatResult, Stop, Tool, ToolCall, ToolTurn, Turn, Usage, UserTurn

log = structlog.get_logger("edda.llm.openai_compat")

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

_STOPS: dict[str, Stop] = {"stop": "end", "tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}


class OpenAICompatibleProvider:
    """Talks to one endpoint. ``client`` is injectable so tests can replay recorded responses."""

    def __init__(
        self,
        name: str,
        config: Endpoint,
        client: httpx.Client | None = None,
        retries: int = 2,
        env: Mapping[str, str] | None = None,
    ) -> None:
        """Connects to ``config.base_url``; ``retries`` covers connection errors and 5xx responses.

        Raises:
            ProviderNotConfigured: The endpoint names a key variable that is not set, or would send its key over
                plain HTTP to another machine.
        """
        self.name = name
        self.config = config
        self.retries = retries
        headers: dict[str, str] = {}
        if config.api_key_env:
            key = (os.environ if env is None else env).get(config.api_key_env)
            if not key:
                raise ProviderNotConfigured(
                    f"the {name} endpoint needs your own API key in {config.api_key_env}: export it in your shell, or "
                    'set the job back to "local" in edda.toml'
                )
            parts = urlsplit(config.base_url)
            if parts.scheme != "https" and parts.hostname not in _LOOPBACK:
                raise ProviderNotConfigured(f"refusing to send the {name} key over plain HTTP to {parts.hostname}")
            headers["Authorization"] = f"Bearer {key}"
        self.client = client or httpx.Client(timeout=config.timeout_seconds)
        self.client.headers.update(headers)

    def chat(
        self, *, model: str, system: str, turns: Sequence[Turn], tools: Sequence[Tool], max_tokens: int
    ) -> ChatResult:
        """Sends one chat-completions request and translates the reply."""
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *_messages(turns)],
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
                }
                for t in tools
            ]
        data = self._post(body)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            calls = [
                ToolCall(id=c["id"], name=c["function"]["name"], arguments=_arguments(c["function"].get("arguments")))
                for c in message.get("tool_calls") or []
            ]
            stop = _STOPS.get(choice.get("finish_reason") or "stop", "end")
            usage = data.get("usage") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response from {self.config.base_url}: {exc!r}") from exc
        if calls and stop == "end":
            stop = "tool_use"
        turn = AssistantTurn(text=message.get("content") or "", tool_calls=calls, native=message)
        return ChatResult(
            turn=turn,
            stop=stop,
            usage=Usage(input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0)),
            provider=self.name,
            model=data.get("model") or model,
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        for attempt in range(self.retries + 1):
            try:
                response = self.client.post(url, json=body)
                if response.status_code < 500:
                    response.raise_for_status()
                    result: dict[str, Any] = response.json()
                    return result
                reason = f"HTTP {response.status_code}"
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    raise ProviderNotConfigured(
                        f"the {self.name} endpoint rejected the API key (HTTP {status})"
                    ) from exc
                if status == 404 and self.config.api_key_env:
                    raise ProviderNotConfigured(
                        f"the {self.name} endpoint has no model {body['model']!r} for this account (HTTP 404); hosted "
                        f"catalogs differ by account, so set another model under [llm.endpoints.{self.name}]"
                    ) from exc
                raise LLMError(f"{url}: HTTP {status}: {exc.response.text[:500]}") from exc
            except (httpx.TransportError, json.JSONDecodeError) as exc:
                reason = repr(exc)
            log.warning("llm_retry", endpoint=self.name, url=url, attempt=attempt + 1, reason=reason)
            if attempt < self.retries:
                time.sleep(min(2**attempt, 10))
        hint = "; is the model server running?" if self.config.api_key_env is None else ""
        raise LLMError(f"{url} did not answer after {self.retries + 1} attempts ({reason}){hint}")


def _arguments(raw: Any) -> dict[str, Any]:
    """Tool arguments arrive as a JSON string (sometimes already parsed); a bad one becomes an empty call."""
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        log.warning("local_llm_bad_tool_arguments", arguments=str(raw)[:200])
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _messages(turns: Sequence[Turn]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for turn in turns:
        if isinstance(turn, UserTurn):
            out.append({"role": "user", "content": turn.text})
        elif isinstance(turn, ToolTurn):
            out.extend({"role": "tool", "tool_call_id": r.call_id, "content": r.content} for r in turn.results)
        elif turn.native is not None:
            out.append(turn.native)
        else:
            calls = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in turn.tool_calls
            ]
            out.append({"role": "assistant", "content": turn.text, **({"tool_calls": calls} if calls else {})})
    return out
