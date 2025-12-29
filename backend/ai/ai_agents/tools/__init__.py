"""Tool system for function calling."""

from .base import Tool, ToolDefinition
from .decorator import tool, FunctionTool
from .registry import (
    register_tool,
    get_tool,
    get_tools,
    get_tool_definitions,
    list_tools,
    clear_tools,
)
from .parser import execute_tool_call, execute_tool_calls
from .builtin import (
    CalculatorTool, 
    WebSearchTool,
    UpdateContextTool,
    set_context_tool_provider,
    clear_context_tool_provider,
    SearchDocumentsTool,
    set_document_context,
    get_sources,
    clear_sources,
    get_search_documents_tool,
)

__all__ = [
    # Base
    "Tool",
    "ToolDefinition",
    # Decorator
    "tool",
    "FunctionTool",
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
    "UpdateContextTool",
    "set_context_tool_provider",
    "clear_context_tool_provider",
    "SearchDocumentsTool",
    "set_document_context",
    "get_sources",
    "clear_sources",
    "get_search_documents_tool",
]
