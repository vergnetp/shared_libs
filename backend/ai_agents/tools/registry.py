"""Tool registry."""

from typing import Type
from .base import Tool


_tools: dict[str, Tool] = {}


def register_tool(tool: Tool):
    """Register a tool instance."""
    _tools[tool.name] = tool


def get_tool(name: str) -> Tool:
    """Get tool by name."""
    if name not in _tools:
        raise ValueError(f"Unknown tool: {name}")
    return _tools[name]


def get_tools(names: list[str]) -> list[Tool]:
    """Get multiple tools by name."""
    return [get_tool(name) for name in names]


def get_tool_definitions(names: list[str]) -> list[dict]:
    """Get tool definitions for LLM."""
    return [get_tool(name).to_dict() for name in names]


def list_tools() -> list[str]:
    """List registered tool names."""
    return list(_tools.keys())


def clear_tools():
    """Clear all registered tools (for testing)."""
    _tools.clear()
