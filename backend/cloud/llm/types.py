"""
LLM API response types.

Provides consistent response structures across all LLM providers.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolCall:
    """A tool/function call from the LLM."""
    id: str
    name: str
    arguments: Dict[str, Any]
    
    @classmethod
    def from_openai(cls, tc: Dict) -> "ToolCall":
        """Create from OpenAI-style tool call."""
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
    def from_anthropic(cls, block: Dict) -> "ToolCall":
        """Create from Anthropic tool_use block."""
        return cls(
            id=block.get("id", ""),
            name=block.get("name", ""),
            arguments=block.get("input", {}),
        )


@dataclass
class ChatResponse:
    """
    Unified response from LLM chat completion.
    
    Works with OpenAI, Anthropic, Groq, and other providers.
    """
    content: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"
    tool_calls: List[ToolCall] = field(default_factory=list)
    raw: Optional[Dict[str, Any]] = None
    
    @property
    def has_tool_calls(self) -> bool:
        """Check if response contains tool calls."""
        return len(self.tool_calls) > 0
    
    @property
    def total_tokens(self) -> int:
        """Total tokens used."""
        return self.input_tokens + self.output_tokens
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "content": self.content,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "finish_reason": self.finish_reason,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in self.tool_calls
            ],
        }


@dataclass
class ChatMessage:
    """A chat message."""
    role: str
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API calls."""
        d = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = self.tool_calls
        return d
