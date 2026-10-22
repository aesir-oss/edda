import json
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
from anthropic.types.beta import BetaMessage

from edda.config import AnthropicLLM, EddaConfig, Endpoint
from edda.llm import (
    LLM,
    AssistantTurn,
    LLMError,
    ProviderNotConfigured,
    Tool,
    ToolCall,
    ToolResult,
    ToolTurn,
    Turn,
    UserTurn,
)
from edda.llm.claude import ClaudeProvider, has_credentials
from edda.llm.openai_compat import OpenAICompatibleProvider
from edda.llm.types import Provider

SEARCH = Tool(name="search", description="Search pages", input_schema={"type": "object", "properties": {}})


LOCAL = Endpoint(base_url="http://127.0.0.1:8080/v1", model="local")


def _local(responses: list[httpx.Response | Exception]) -> tuple[OpenAICompatibleProvider, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []
    queue = list(responses)

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return OpenAICompatibleProvider("local", LOCAL, client=client, retries=1), sent


def _completion(message: dict[str, Any], finish: str = "stop") -> httpx.Response:
    body = {
        "model": "qwen-local",
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }
    return httpx.Response(200, json=body)


def test_local_provider_speaks_openai_compatible_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("edda.llm.openai_compat.time.sleep", lambda _: None)
    call = {"id": "c1", "type": "function", "function": {"name": "search", "arguments": '{"query": "attention"}'}}
    provider, sent = _local(
        [
            httpx.ConnectError("refused"),
            _completion({"role": "assistant", "content": None, "tool_calls": [call]}, "tool_calls"),
        ]
    )
    result = provider.chat(model="local", system="be exact", turns=[UserTurn(text="hi")], tools=[SEARCH], max_tokens=50)
    assert result.stop == "tool_use"
    assert result.turn.tool_calls == [ToolCall(id="c1", name="search", arguments={"query": "attention"})]
    assert (result.usage.input_tokens, result.usage.output_tokens, result.model) == (120, 30, "qwen-local")
    assert sent[0]["messages"][0] == {"role": "system", "content": "be exact"}
    assert sent[0]["tools"][0]["function"]["name"] == "search"

    provider, sent = _local([_completion({"role": "assistant", "content": "done"})])
    turns: list[Turn] = [
        UserTurn(text="hi"),
        result.turn,
        ToolTurn(results=[ToolResult(call_id="c1", content="[]")]),
        AssistantTurn(text="plain", tool_calls=[ToolCall(id="c2", name="search", arguments={})]),
    ]
    final = provider.chat(model="local", system="s", turns=turns, tools=[], max_tokens=50)
    assert (final.stop, final.turn.text) == ("end", "done")
    roles = [m["role"] for m in sent[0]["messages"]]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert sent[0]["messages"][4]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert "tools" not in sent[0]


@pytest.mark.parametrize("arguments", ["not json", "[1, 2]", None])
def test_local_provider_tolerates_bad_tool_arguments(arguments: Any) -> None:
    call = {"id": "c1", "function": {"name": "search", "arguments": arguments}}
    provider, _ = _local([_completion({"content": "", "tool_calls": [call]})])
    result = provider.chat(model="m", system="s", turns=[UserTurn(text="x")], tools=[], max_tokens=5)
    assert (result.stop, result.turn.tool_calls[0].arguments) == ("tool_use", {})


def test_local_provider_reports_unreachable_and_broken_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("edda.llm.openai_compat.time.sleep", lambda _: None)
    provider, _ = _local([httpx.Response(503), httpx.ConnectError("refused")])
    with pytest.raises(LLMError, match="is the model server running"):
        provider.chat(model="m", system="s", turns=[], tools=[], max_tokens=5)
    provider, _ = _local([httpx.Response(400, text="bad model")])
    with pytest.raises(LLMError, match="HTTP 400: bad model"):
        provider.chat(model="m", system="s", turns=[], tools=[], max_tokens=5)
    provider, _ = _local([httpx.Response(200, json={"choices": []})])
    with pytest.raises(LLMError, match="unexpected response"):
        provider.chat(model="m", system="s", turns=[], tools=[], max_tokens=5)
    assert OpenAICompatibleProvider("local", LOCAL).client is not None


class _FakeMessages:
    def __init__(self, replies: list[BetaMessage | Exception]) -> None:
        self.replies = replies
        self.sent: list[dict[str, Any]] = []

    def create(self, **params: Any) -> BetaMessage:
        self.sent.append(params)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class _FakeClient:
    def __init__(self, replies: list[BetaMessage | Exception]) -> None:
        self.beta = type("Beta", (), {"messages": _FakeMessages(replies)})()


def _message(content: list[dict[str, Any]], stop: str, model: str = "claude-sonnet-5-5") -> BetaMessage:
    return BetaMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 50,
                "output_tokens": 20,
                "cache_read_input_tokens": 400,
                "cache_creation_input_tokens": 0,
            },
        }
    )


def test_claude_provider_caches_the_prefix_and_replays_turns_unchanged() -> None:
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    tool_use = {"type": "tool_use", "id": "tu1", "name": "search", "input": {"query": "bm25"}}
    client = _FakeClient(
        [_message([thinking, tool_use], "tool_use"), _message([{"type": "text", "text": "ok"}], "end_turn")]
    )
    provider = ClaudeProvider(AnthropicLLM(), client=client)
    first = provider.chat(
        model="claude-sonnet-5-5", system="rules", turns=[UserTurn(text="go")], tools=[SEARCH], max_tokens=99
    )
    assert first.stop == "tool_use"
    assert first.turn.tool_calls == [ToolCall(id="tu1", name="search", arguments={"query": "bm25"})]
    assert (first.usage.cache_read_tokens, first.provider) == (400, "anthropic")
    sent = client.beta.messages.sent[0]
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert (sent["fallbacks"], sent["betas"]) == ("default", ["server-side-fallback-2026-07-01"])

    turns: list[Turn] = [
        UserTurn(text="go"),
        first.turn,
        ToolTurn(results=[ToolResult(call_id="tu1", content="none", is_error=True)]),
    ]
    second = provider.chat(model="claude-haiku-5-5", system="rules", turns=turns, tools=[], max_tokens=99)
    assert (second.stop, second.turn.text) == ("end", "ok")
    replay = client.beta.messages.sent[1]
    assert replay["messages"][1]["content"][0]["signature"] == "sig", "thinking blocks go back exactly as received"
    assert replay["messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "tu1",
        "content": "none",
        "is_error": True,
    }
    assert "fallbacks" not in replay and "tools" not in replay


def test_claude_provider_rebuilds_turns_without_native_content() -> None:
    client = _FakeClient([_message([{"type": "text", "text": "fine"}], "refusal")])
    provider = ClaudeProvider(AnthropicLLM(fallbacks=False), client=client)
    turn = AssistantTurn(text="I will search", tool_calls=[ToolCall(id="t", name="search", arguments={})])
    result = provider.chat(
        model="claude-opus-5-5", system="s", turns=[UserTurn(text="q"), turn], tools=[], max_tokens=9
    )
    assert result.stop == "refusal"
    blocks = client.beta.messages.sent[0]["messages"][1]["content"]
    assert [b["type"] for b in blocks] == ["text", "tool_use"]
    assert "fallbacks" not in client.beta.messages.sent[0]


def test_claude_provider_maps_sdk_errors() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    auth = anthropic.AuthenticationError("bad key", response=httpx2.Response(401, request=request), body=None)
    status = anthropic.InternalServerError("boom", response=httpx2.Response(500, request=request), body=None)
    connection = anthropic.APIConnectionError(request=request)
    for error, expected in [(auth, ProviderNotConfigured), (status, LLMError), (connection, LLMError)]:
        provider = ClaudeProvider(AnthropicLLM(), client=_FakeClient([error]))
        with pytest.raises(expected):
            provider.chat(model="claude-haiku-5-5", system="s", turns=[], tools=[], max_tokens=1)


def test_credentials_come_only_from_the_environment_or_a_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert not has_credentials({}, tmp_path)
    assert has_credentials({"ANTHROPIC_API_KEY": "sk-test"}, tmp_path)
    (tmp_path / ".config" / "anthropic").mkdir(parents=True)
    assert has_credentials({}, tmp_path)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("edda.llm.claude.Path.home", lambda: tmp_path / "nobody")
    with pytest.raises(ProviderNotConfigured, match="your own API key"):
        ClaudeProvider(AnthropicLLM())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    assert ClaudeProvider(AnthropicLLM()).client is not None


class _Scripted:
    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.models: list[str] = []

    def chat(self, *, model: str, system: str, turns: Any, tools: Any, max_tokens: int) -> Any:
        from edda.llm import ChatResult, Usage

        self.models.append(model)
        return ChatResult(
            turn=AssistantTurn(text="x"),
            stop="end",
            usage=Usage(input_tokens=10, output_tokens=max_tokens),
            provider=self.provider,
            model=model,
        )


def test_the_facade_picks_providers_per_job_and_keeps_a_ledger() -> None:
    config = EddaConfig.model_validate(
        {"llm": {"lint": "anthropic", "endpoints": {"local": {"model": "qwen"}}}, "budgets": {"max_output_tokens": 7}}
    )
    local, claude = _Scripted("local"), _Scripted("anthropic")
    llm = LLM(config, providers={"local": local, "anthropic": claude})
    assert llm.provider_for("ingest") == ("local", "qwen")
    assert llm.provider_for("lint") == ("anthropic", "claude-opus-5-5")
    llm.chat("ingest", "ingest:1", system="s", turns=[UserTurn(text="q")])
    llm.chat("ingest", "ingest:1", system="s", turns=[UserTurn(text="q")])
    llm.chat("lint", "lint:1", system="s", turns=[UserTurn(text="q")])
    assert (local.models, claude.models) == (["qwen", "qwen"], ["claude-opus-5-5"])
    assert llm.usage("ingest:1").output_tokens == 14
    assert [r.provider for r in llm.ledger] == ["local", "local", "anthropic"]
    assert llm.usage("nothing").input_tokens == 0


def test_the_facade_creates_providers_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("edda.llm.claude.Path.home", lambda: Path("/nonexistent"))
    llm = LLM(EddaConfig.model_validate({"llm": {"query": "anthropic"}}))
    assert isinstance(llm._provider("local"), OpenAICompatibleProvider)
    with pytest.raises(ProviderNotConfigured):
        llm.chat("query", "query:1", system="s", turns=[])


NVIDIA = Endpoint(
    base_url="https://integrate.api.nvidia.com/v1",
    model="nvidia/nemotron-3-super-120b-a12b",
    api_key_env="NVIDIA_API_KEY",
)


def test_hosted_endpoints_send_the_key_from_the_environment() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _completion({"role": "assistant", "content": "hi"})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    provider = OpenAICompatibleProvider("nvidia", NVIDIA, client=client, env={"NVIDIA_API_KEY": "nvapi-test"})
    result = provider.chat(model=NVIDIA.model, system="s", turns=[UserTurn(text="q")], tools=[], max_tokens=5)
    assert result.provider == "nvidia"
    assert seen[0].headers["authorization"] == "Bearer nvapi-test"
    assert str(seen[0].url) == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert json.loads(seen[0].content)["model"] == "nvidia/nemotron-3-super-120b-a12b"


def test_hosted_endpoints_need_a_key_and_https() -> None:
    with pytest.raises(ProviderNotConfigured, match="your own API key in NVIDIA_API_KEY"):
        OpenAICompatibleProvider("nvidia", NVIDIA, env={})
    plain = Endpoint(base_url="http://models.example.net/v1", model="m", api_key_env="KEY")
    with pytest.raises(ProviderNotConfigured, match=r"plain HTTP to models\.example\.net"):
        OpenAICompatibleProvider("remote", plain, env={"KEY": "k"})
    loopback = Endpoint(base_url="http://localhost:11434/v1", model="m", api_key_env="KEY")
    assert OpenAICompatibleProvider("ollama", loopback, env={"KEY": "k"}).client.headers["authorization"] == "Bearer k"


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_says_so(status: int) -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text="denied")))
    provider = OpenAICompatibleProvider("nvidia", NVIDIA, client=client, env={"NVIDIA_API_KEY": "bad"}, retries=0)
    with pytest.raises(ProviderNotConfigured, match=f"rejected the API key \\(HTTP {status}\\)"):
        provider.chat(model="m", system="s", turns=[], tools=[], max_tokens=5)


def test_hosted_endpoints_do_not_blame_a_local_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("edda.llm.openai_compat.time.sleep", lambda _: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    provider = OpenAICompatibleProvider("nvidia", NVIDIA, client=client, env={"NVIDIA_API_KEY": "k"}, retries=0)
    with pytest.raises(LLMError) as raised:
        provider.chat(model="m", system="s", turns=[], tools=[], max_tokens=5)
    assert "model server running" not in str(raised.value)


def test_the_facade_routes_jobs_to_named_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    config = EddaConfig.model_validate({"llm": {"ingest": "nvidia"}})
    assert LLM(config).provider_for("ingest") == ("nvidia", "nvidia/nemotron-3-super-120b-a12b")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(ProviderNotConfigured, match="NVIDIA_API_KEY"):
        LLM(config).chat("ingest", "ingest:1", system="s", turns=[])
