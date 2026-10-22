"""The provider-neutral shapes jobs use to talk to a model: tools, turns, results and token use."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Job = Literal["ingest", "query", "lint", "research", "summary"]
# An endpoint name from ``[llm.endpoints]`` (``local``, ``nvidia``, ...) or ``anthropic``.
Provider = str
Stop = Literal["end", "tool_use", "max_tokens", "refusal"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class Tool(_Model):
    """A system call offered to the model: a name, what it does, and a JSON Schema for its arguments."""

    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(_Model):
    """The model asking to run a tool."""

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(_Model):
    """What running a tool returned, for the call with ``call_id``."""

    call_id: str
    content: str
    is_error: bool = False


class UserTurn(_Model):
    """A message from the job to the model."""

    role: Literal["user"] = "user"
    text: str


class AssistantTurn(_Model):
    """A model reply. ``native`` is the provider's own form of it, replayed unchanged on the next request."""

    role: Literal["assistant"] = "assistant"
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    native: Any = None


class ToolTurn(_Model):
    """The results of the tools the model called in its last turn."""

    role: Literal["tool"] = "tool"
    results: list[ToolResult]


Turn = Annotated[UserTurn | AssistantTurn | ToolTurn, Field(discriminator="role")]


class Usage(_Model):
    """Tokens one call used. Cache counts are zero for providers without a prompt cache."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        """Adds two calls' token counts."""
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class ChatResult(_Model):
    """One model call's reply, why it stopped, and what it cost in tokens."""

    turn: AssistantTurn
    stop: Stop
    usage: Usage
    provider: Provider
    model: str


class CallRecord(_Model):
    """One entry of the token ledger."""

    job: Job
    job_id: str
    provider: Provider
    model: str
    stop: Stop
    usage: Usage
