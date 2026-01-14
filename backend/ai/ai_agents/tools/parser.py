from __future__ import annotations
"""Tool call execution."""

from typing import Any

# Backend imports (absolute - backend must be in sys.path)
try:
    from log import info, error
except ImportError:
    def info(msg, **kwargs): pass
    def error(msg, **kwargs): 
        print(f"[ERROR] {msg}")
        if 'traceback' in kwargs:
            print(kwargs['traceback'])

# Local imports
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
    arguments = tool_call.get("arguments") or {}  # Handle None from Groq/Llama
    
    print(f"[DEBUG execute_tool_call] tool_call={tool_call}")
    print(f"[DEBUG execute_tool_call] arguments type={type(arguments)}, value={arguments}")
    
    info("Executing tool", tool=tool_name, args=arguments)
    
    try:
        tool = get_tool(tool_name)
        print(f"[DEBUG execute_tool_call] Got tool {tool_name}, calling execute with {arguments}")
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
        import traceback
        error("Tool execution failed", tool=tool_name, error=str(e), traceback=traceback.format_exc())
        return ToolResult(
            tool_call_id=tool_call_id,
            content=f"Error: {str(e)}",
            is_error=True,
        )


async def execute_tool_calls(tool_calls: list[dict]) -> list[ToolResult]:
    """Execute multiple tool calls in parallel."""
    import asyncio
    
    if not tool_calls:
        return []
    
    # Execute all tool calls concurrently
    results = await asyncio.gather(
        *[execute_tool_call(tc) for tc in tool_calls],
        return_exceptions=True,
    )
    
    # Convert exceptions to error results
    final_results = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            tc = tool_calls[i]
            final_results.append(ToolResult(
                tool_call_id=tc.get("id", f"error_{i}"),
                content=f"Error: {str(result)}",
                is_error=True,
            ))
        else:
            final_results.append(result)
    
    return final_results
