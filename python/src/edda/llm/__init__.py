"""The model layer: local models by default, Anthropic when a job selects it and the person supplies their own key."""

from edda.llm.client import LLM
from edda.llm.errors import LLMError, ProviderNotConfigured
from edda.llm.types import (
    AssistantTurn,
    CallRecord,
    ChatResult,
    Job,
    Tool,
    ToolCall,
    ToolResult,
    ToolTurn,
    Turn,
    Usage,
    UserTurn,
)

__all__ = [
    "LLM",
    "AssistantTurn",
    "CallRecord",
    "ChatResult",
    "Job",
    "LLMError",
    "ProviderNotConfigured",
    "Tool",
    "ToolCall",
    "ToolResult",
    "ToolTurn",
    "Turn",
    "Usage",
    "UserTurn",
]
