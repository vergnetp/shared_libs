from __future__ import annotations
"""Tool registry."""

from typing import Type
from .base import Tool


_tools: dict[str, Tool] = {}


def register_tool(tool: Tool):
    """Register a tool instance."""
    print(f"[DEBUG register_tool] Registering {tool.name}, current tools: {list(_tools.keys())}")
    _tools[tool.name] = tool
    print(f"[DEBUG register_tool] After registration: {list(_tools.keys())}")


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
    print(f"[DEBUG get_tool_definitions] names={names}, registered={list(_tools.keys())}")
    result = []
    for name in names:
        try:
            tool = get_tool(name)
            defn = tool.to_dict()
            print(f"[DEBUG get_tool_definitions] {name} -> {defn}")
            result.append(defn)
        except Exception as e:
            print(f"[DEBUG get_tool_definitions] ERROR for {name}: {e}")
            import traceback
            traceback.print_exc()
            result.append(None)
    return result


def list_tools() -> list[str]:
    """List registered tool names."""
    return list(_tools.keys())


def clear_tools():
    """Clear all registered tools (for testing)."""
    _tools.clear()
