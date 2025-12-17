"""Tool system for function calling."""

from .base import Tool, ToolDefinition
from .registry import (
    register_tool,
    get_tool,
    get_tools,
    get_tool_definitions,
    list_tools,
    clear_tools,
)
from .parser import execute_tool_call, execute_tool_calls
from .builtin import CalculatorTool, WebSearchTool

__all__ = [
    # Base
    "Tool",
    "ToolDefinition",
    # Registry
    "register_tool",
    "get_tool",
    "get_tools",
    "get_tool_definitions",
    "list_tools",
    "clear_tools",
    # Execution
    "execute_tool_call",
    "execute_tool_calls",
    # Built-in
    "CalculatorTool",
    "WebSearchTool",
]
