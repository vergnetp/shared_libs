"""Core types for AI agents."""
from __future__ import annotations

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


@dataclass
class ChatResult:
    """Result from Agent.chat() with all metadata for auditing."""
    content: str
    usage: dict  # {"input": int, "output": int}
    cost: float
    duration_ms: int
    model: str
    provider: str
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)  # Tool outputs for UI display
    tools_used: list[str] = field(default_factory=list)
    # Overrides used for this request (for audit)
    temperature: float = None
    stick_to_facts: bool = None
    objective_responses: bool = None
    memory_strategy: str = None
    memory_n: int = None
    
    def to_metadata(self) -> dict:
        """Convert to metadata dict for storage."""
        meta = {
            "usage": self.usage,
            "cost": self.cost,
            "duration_ms": self.duration_ms,
            "model": self.model,
            "provider": self.provider,
            "tools_used": self.tools_used,
            "call_type": "chat",
        }
        # Add overrides if set (for audit trail)
        if self.temperature is not None:
            meta["temperature"] = self.temperature
        if self.stick_to_facts is not None:
            meta["stick_to_facts"] = self.stick_to_facts
        if self.objective_responses is not None:
            meta["objective_responses"] = self.objective_responses
        if self.memory_strategy is not None:
            meta["memory_strategy"] = self.memory_strategy
        if self.memory_n is not None:
            meta["memory_n"] = self.memory_n
        return meta
