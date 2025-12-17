"""Tool decorator for easy tool creation."""

import inspect
import functools
from typing import Callable, Any, get_type_hints

from .base import Tool, ToolDefinition
from .registry import register_tool


def tool(
    description: str = None,
    name: str = None,
    auto_register: bool = True,
):
    """
    Decorator to create tools from functions.
    
    Example:
        @tool(description="Search documents for information")
        async def search_documents(query: str, top_k: int = 5) -> list[dict]:
            '''Search the knowledge base.'''
            return await searcher.search_only(query, top_k=top_k)
        
        @tool(description="Calculate math expression")
        def calculate(expression: str) -> str:
            return str(eval(expression))  # Don't actually do this
        
        # Use in agent
        agent = Agent(..., tools=["search_documents", "calculate"])
    
    Args:
        description: Tool description for LLM. If not provided, uses docstring.
        name: Tool name. If not provided, uses function name.
        auto_register: Whether to auto-register the tool. Default True.
    """
    def decorator(func: Callable) -> "FunctionTool":
        tool_name = name or func.__name__
        tool_description = description or func.__doc__ or f"Tool: {tool_name}"
        
        # Build parameters schema from function signature
        parameters = _build_parameters_schema(func)
        
        # Create tool instance
        tool_instance = FunctionTool(
            func=func,
            tool_name=tool_name,
            tool_description=tool_description,
            parameters=parameters,
        )
        
        if auto_register:
            register_tool(tool_instance)
        
        return tool_instance
    
    return decorator


class FunctionTool(Tool):
    """Tool created from a function via @tool decorator."""
    
    def __init__(
        self,
        func: Callable,
        tool_name: str,
        tool_description: str,
        parameters: dict,
    ):
        self._func = func
        self._name = tool_name
        self._description = tool_description
        self._parameters = parameters
        self._is_async = inspect.iscoroutinefunction(func)
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def description(self) -> str:
        return self._description
    
    async def execute(self, **kwargs) -> Any:
        if self._is_async:
            return await self._func(**kwargs)
        else:
            return self._func(**kwargs)
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description=self._description,
            parameters=self._parameters,
        )
    
    def __call__(self, *args, **kwargs):
        """Allow direct function calls."""
        return self._func(*args, **kwargs)
    
    def __repr__(self) -> str:
        return f"FunctionTool({self._name})"


def _build_parameters_schema(func: Callable) -> dict:
    """Build JSON schema from function signature."""
    sig = inspect.signature(func)
    
    # Try to get type hints
    try:
        hints = get_type_hints(func)
    except:
        hints = {}
    
    properties = {}
    required = []
    
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        
        # Get type
        param_type = hints.get(param_name, Any)
        json_type = _python_type_to_json(param_type)
        
        # Build property
        prop = {"type": json_type}
        
        # Add description from docstring if available
        # (could parse docstring for param descriptions)
        
        properties[param_name] = prop
        
        # Required if no default
        if param.default is inspect.Parameter.empty:
            required.append(param_name)
    
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _python_type_to_json(python_type) -> str:
    """Convert Python type to JSON schema type."""
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    
    # Handle typing module types
    origin = getattr(python_type, "__origin__", None)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    
    return type_map.get(python_type, "string")
