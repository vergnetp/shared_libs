"""Tool call execution."""

from typing import Any
from shared_lib.logging import info, error

from ..core import ToolResult, ToolError
from .registry import get_tool


async def execute_tool_call(tool_call: dict) -> ToolResult:
    """
    Execute a single tool call.
    
    Args:
        tool_call: {"id": str, "name": str, "arguments": dict}
        
    Returns:
        ToolResult with output or error
    """
    tool_call_id = tool_call["id"]
    tool_name = tool_call["name"]
    arguments = tool_call.get("arguments", {})
    
    info("Executing tool", tool=tool_name, args=arguments)
    
    try:
        tool = get_tool(tool_name)
        result = await tool.execute(**arguments)
        
        # Convert result to string
        if not isinstance(result, str):
            import json
            result = json.dumps(result, default=str)
        
        info("Tool completed", tool=tool_name)
        return ToolResult(tool_call_id=tool_call_id, content=result)
        
    except ValueError as e:
        # Tool not found
        error("Tool not found", tool=tool_name)
        return ToolResult(
            tool_call_id=tool_call_id,
            content=f"Error: Tool '{tool_name}' not found",
            is_error=True,
        )
    except Exception as e:
        error("Tool execution failed", tool=tool_name, error=str(e))
        return ToolResult(
            tool_call_id=tool_call_id,
            content=f"Error: {str(e)}",
            is_error=True,
        )


async def execute_tool_calls(tool_calls: list[dict]) -> list[ToolResult]:
    """Execute multiple tool calls."""
    results = []
    for tc in tool_calls:
        result = await execute_tool_call(tc)
        results.append(result)
    return results
