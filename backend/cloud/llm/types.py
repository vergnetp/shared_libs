"""
LLM Client Types - Simple dataclasses for API responses.

These are raw API responses. The `ai` module wraps these with:
- Token counting
- Tool call normalization
- Provider-specific quirks
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A tool/function call from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]
    
    @classmethod
    def from_openai(cls, tc: dict) -> "ToolCall":
        """Parse OpenAI-style tool call."""
        import json
        func = tc.get("function", {})
        args = func.get("arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return cls(
            id=tc.get("id", ""),
            name=func.get("name", ""),
            arguments=args,
        )
    
    @classmethod
    def from_anthropic(cls, block: dict) -> "ToolCall":
        """Parse Anthropic-style tool_use block."""
        return cls(
            id=block.get("id", ""),
            name=block.get("name", ""),
            arguments=block.get("input", {}),
        )
    
    def to_openai(self) -> dict:
        """Convert to OpenAI format for message history."""
        import json
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments),
            }
        }


@dataclass
class ChatResponse:
    """
    Response from an LLM chat completion.
    
    This is a raw API response. Provider-specific normalization
    (XML tool parsing, etc.) happens in the `ai` module.
    """
    content: str
    """Text content from the response."""
    
    model: str
    """Model that generated the response."""
    
    input_tokens: int
    """Number of input/prompt tokens."""
    
    output_tokens: int
    """Number of output/completion tokens."""
    
    finish_reason: str
    """Why generation stopped: 'stop', 'tool_calls', 'length', 'content_filter'."""
    
    tool_calls: list[ToolCall] = field(default_factory=list)
    """Tool calls requested by the model (if any)."""
    
    raw: dict = field(default_factory=dict, repr=False)
    """Original API response for debugging."""
    
    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0
    
    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return self.input_tokens + self.output_tokens


@dataclass
class StreamChunk:
    """
    A chunk from a streaming response.
    
    For simple text streaming, just use the async iterator which yields strings.
    This class is for cases where you need metadata (tool calls in stream, etc.)
    """
    content: str = ""
    """Text content in this chunk."""
    
    finish_reason: str | None = None
    """Set on the final chunk."""
    
    tool_calls_delta: list[dict] = field(default_factory=list)
    """Partial tool call data (for advanced use)."""
