"""Core types for AI agents."""

from dataclasses import dataclass, field
from typing import Optional, Any
from enum import Enum


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: MessageRole
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    tool_call_id: str = None
    name: str = None  # For tool messages
    attachments: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class ProviderResponse:
    content: str
    usage: dict  # {"input": int, "output": int}
    model: str
    provider: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = None
    raw: Any = None  # Original provider response


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ToolResult:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class AgentConfig:
    """Configuration for an agent."""
    name: str
    system_prompt: str
    model: str = "claude-sonnet-4-20250514"
    provider: str = "anthropic"
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: list[str] = field(default_factory=list)
    memory_strategy: str = "last_n"
    memory_params: dict = field(default_factory=lambda: {"n": 20})


@dataclass
class ThreadConfig:
    """Runtime config for a thread, can override agent defaults."""
    temperature: float = None
    max_tokens: int = None
    tools: list[str] = None
    memory_strategy: str = None
    memory_params: dict = None
