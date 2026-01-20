"""
Instructor integration for structured LLM outputs.

Instructor forces LLM outputs to match Pydantic models with automatic retries.
This eliminates malformed JSON and XML parsing issues from Llama/Groq/Ollama.

Usage:
    from ai_agents.providers.instructor_support import (
        enable_instructor,
        ToolCallList,
        StructuredResponse,
    )
    
    # Wrap existing provider
    provider = enable_instructor(OpenAIProvider(api_key="..."))
    
    # Get guaranteed structured output
    result = await provider.complete_structured(
        messages=[...],
        response_model=ToolCallList,
    )

Requires: pip install instructor
"""
from __future__ import annotations

from typing import Any, TypeVar, Type, Optional, List, Union
from dataclasses import dataclass

# Pydantic models for structured outputs
try:
    from pydantic import BaseModel, Field
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = object
    def Field(*args, **kwargs): return None

# Instructor library
try:
    import instructor
    INSTRUCTOR_AVAILABLE = True
except ImportError:
    INSTRUCTOR_AVAILABLE = False
    instructor = None


# =============================================================================
# PYDANTIC MODELS FOR STRUCTURED OUTPUTS
# =============================================================================

if PYDANTIC_AVAILABLE:
    
    class ToolCallModel(BaseModel):
        """Single tool call with validated structure."""
        name: str = Field(description="Name of the tool to call")
        arguments: dict = Field(default_factory=dict, description="Arguments as key-value pairs")
        
        def to_dict(self, id_prefix: str = "tc") -> dict:
            """Convert to internal tool call format."""
            import hashlib
            # Generate stable ID from name + args
            args_str = str(sorted(self.arguments.items()))
            hash_input = f"{self.name}:{args_str}"
            tc_id = f"{id_prefix}_{hashlib.md5(hash_input.encode()).hexdigest()[:8]}"
            return {
                "id": tc_id,
                "name": self.name,
                "arguments": self.arguments,
            }
    
    
    class ToolCallList(BaseModel):
        """List of tool calls - use when model should call tools."""
        tool_calls: List[ToolCallModel] = Field(
            default_factory=list,
            description="List of tools to call. Empty if no tools needed."
        )
        reasoning: Optional[str] = Field(
            default=None,
            description="Brief reasoning for tool selection (optional)"
        )
        
        def to_internal_format(self) -> list[dict]:
            """Convert to internal tool call list format."""
            return [tc.to_dict(f"inst_{i}") for i, tc in enumerate(self.tool_calls)]
    
    
    class TextResponse(BaseModel):
        """Plain text response - use when no tools needed."""
        content: str = Field(description="The response text")
        
    
    class StructuredResponse(BaseModel):
        """Response that may contain either text or tool calls."""
        content: Optional[str] = Field(
            default=None,
            description="Text response (if not calling tools)"
        )
        tool_calls: List[ToolCallModel] = Field(
            default_factory=list,
            description="Tools to call (if any)"
        )
        
        @property
        def has_tool_calls(self) -> bool:
            return len(self.tool_calls) > 0
        
        def to_internal_format(self) -> tuple[str, list[dict]]:
            """Convert to (content, tool_calls) tuple."""
            tool_calls = [tc.to_dict(f"inst_{i}") for i, tc in enumerate(self.tool_calls)]
            return (self.content or "", tool_calls)
    
    
    class ExtractedEntities(BaseModel):
        """Generic entity extraction result."""
        entities: List[dict] = Field(default_factory=list)
        
    
    class Classification(BaseModel):
        """Classification result with confidence."""
        label: str = Field(description="The classification label")
        confidence: float = Field(ge=0, le=1, description="Confidence score 0-1")
        reasoning: Optional[str] = Field(default=None)

else:
    # Stub classes when Pydantic not available
    class ToolCallModel:
        pass
    class ToolCallList:
        pass
    class TextResponse:
        pass
    class StructuredResponse:
        pass
    class ExtractedEntities:
        pass
    class Classification:
        pass


# =============================================================================
# INSTRUCTOR WRAPPER
# =============================================================================

T = TypeVar('T', bound=BaseModel)


class InstructorMixin:
    """
    Mixin that adds structured output capability to any provider.
    
    Wraps the provider's client with Instructor for guaranteed schema compliance.
    """
    
    _instructor_client: Any = None
    _instructor_mode: str = "tool_call"  # or "json" or "md_json"
    
    async def complete_structured(
        self,
        messages: list[dict],
        response_model: Type[T],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_retries: int = 2,
        **kwargs,
    ) -> T:
        """
        Get structured output that matches the Pydantic model.
        
        Args:
            messages: Conversation messages
            response_model: Pydantic model class for response
            temperature: Sampling temperature
            max_tokens: Max tokens
            max_retries: Retries on validation failure
            
        Returns:
            Instance of response_model with validated data
            
        Raises:
            ValueError: If Instructor not available
            ValidationError: If response doesn't match schema after retries
        """
        if not INSTRUCTOR_AVAILABLE:
            raise ValueError(
                "Instructor not installed. Run: pip install instructor"
            )
        
        if self._instructor_client is None:
            raise ValueError(
                "Instructor not enabled for this provider. "
                "Use enable_instructor(provider) first."
            )
        
        # Use instructor client for structured output
        return await self._instructor_client.chat.completions.create(
            model=self.model,
            messages=messages,
            response_model=response_model,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            **kwargs,
        )
    
    async def complete_with_tools_structured(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> StructuredResponse:
        """
        Complete with guaranteed tool call structure.
        
        Eliminates XML parsing issues by forcing structured output.
        
        Args:
            messages: Conversation messages
            tools: Tool definitions (used to build prompt context)
            temperature: Sampling temperature
            max_tokens: Max tokens
            
        Returns:
            StructuredResponse with validated tool_calls or content
        """
        # Add tool context to system message
        tool_context = self._build_tool_context(tools)
        enhanced_messages = self._inject_tool_context(messages, tool_context)
        
        return await self.complete_structured(
            messages=enhanced_messages,
            response_model=StructuredResponse,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
    
    def _build_tool_context(self, tools: list[dict]) -> str:
        """Build tool description for prompt."""
        if not tools:
            return ""
        
        lines = ["Available tools:"]
        for tool in tools:
            name = tool.get("name", "unknown")
            desc = tool.get("description", "No description")
            params = tool.get("parameters", {}).get("properties", {})
            
            param_strs = []
            for pname, pinfo in params.items():
                ptype = pinfo.get("type", "any")
                pdesc = pinfo.get("description", "")
                param_strs.append(f"  - {pname} ({ptype}): {pdesc}")
            
            lines.append(f"\n{name}: {desc}")
            if param_strs:
                lines.extend(param_strs)
        
        return "\n".join(lines)
    
    def _inject_tool_context(
        self, 
        messages: list[dict], 
        tool_context: str,
    ) -> list[dict]:
        """Inject tool context into messages."""
        if not tool_context:
            return messages
        
        messages = list(messages)
        
        # Find or create system message
        if messages and messages[0].get("role") == "system":
            messages[0] = {
                **messages[0],
                "content": f"{messages[0]['content']}\n\n{tool_context}"
            }
        else:
            messages.insert(0, {"role": "system", "content": tool_context})
        
        return messages


def enable_instructor(
    provider,
    mode: str = "tool_call",
) -> Any:
    """
    Enable Instructor for a provider.
    
    Args:
        provider: LLM provider instance (OpenAI, Anthropic, etc.)
        mode: Instructor mode - "tool_call", "json", or "md_json"
        
    Returns:
        Provider with complete_structured method available
        
    Example:
        provider = enable_instructor(OpenAIProvider(api_key="..."))
        result = await provider.complete_structured(messages, ToolCallList)
    """
    if not INSTRUCTOR_AVAILABLE:
        raise ImportError(
            "Instructor not installed. Run: pip install instructor"
        )
    
    if not PYDANTIC_AVAILABLE:
        raise ImportError(
            "Pydantic not installed. Run: pip install pydantic"
        )
    
    # Determine provider type and wrap appropriately
    provider_name = getattr(provider, 'name', '')
    client = getattr(provider, 'client', None)
    
    if client is None:
        raise ValueError(f"Provider {provider_name} has no client attribute")
    
    # Wrap client with Instructor
    if provider_name in ("openai", "openai_assistant"):
        instructor_client = instructor.from_openai(client)
    elif provider_name == "anthropic":
        instructor_client = instructor.from_anthropic(client)
    elif provider_name == "groq":
        # Groq uses OpenAI-compatible API
        instructor_client = instructor.from_groq(client)
    elif provider_name == "ollama":
        # Ollama uses OpenAI-compatible API
        instructor_client = instructor.from_openai(
            client,
            mode=instructor.Mode.JSON,  # Ollama works best with JSON mode
        )
    else:
        # Try generic OpenAI-compatible wrapping
        try:
            instructor_client = instructor.from_openai(client)
        except Exception as e:
            raise ValueError(
                f"Cannot enable Instructor for provider '{provider_name}': {e}"
            )
    
    # Add mixin methods to provider instance
    provider._instructor_client = instructor_client
    provider._instructor_mode = mode
    
    # Bind mixin methods
    import types
    provider.complete_structured = types.MethodType(
        InstructorMixin.complete_structured, provider
    )
    provider.complete_with_tools_structured = types.MethodType(
        InstructorMixin.complete_with_tools_structured, provider
    )
    provider._build_tool_context = types.MethodType(
        InstructorMixin._build_tool_context, provider
    )
    provider._inject_tool_context = types.MethodType(
        InstructorMixin._inject_tool_context, provider
    )
    
    return provider


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def extract_tool_calls(
    provider,
    messages: list[dict],
    tools: list[dict],
    **kwargs,
) -> list[dict]:
    """
    Extract tool calls with guaranteed valid structure.
    
    Convenience function that handles Instructor setup internally.
    Falls back to regular completion if Instructor unavailable.
    
    Args:
        provider: LLM provider
        messages: Conversation messages
        tools: Tool definitions
        
    Returns:
        List of tool calls in internal format: [{"id", "name", "arguments"}]
    """
    if not INSTRUCTOR_AVAILABLE or not PYDANTIC_AVAILABLE:
        # Fall back to regular completion
        response = await provider.run(messages=messages, tools=tools, **kwargs)
        return response.tool_calls or []
    
    # Enable instructor if not already
    if not hasattr(provider, '_instructor_client') or provider._instructor_client is None:
        provider = enable_instructor(provider)
    
    result = await provider.complete_with_tools_structured(
        messages=messages,
        tools=tools,
        **kwargs,
    )
    
    return result.to_internal_format()[1]


def is_instructor_available() -> bool:
    """Check if Instructor is available."""
    return INSTRUCTOR_AVAILABLE and PYDANTIC_AVAILABLE


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Models
    "ToolCallModel",
    "ToolCallList", 
    "TextResponse",
    "StructuredResponse",
    "ExtractedEntities",
    "Classification",
    # Functions
    "enable_instructor",
    "extract_tool_calls",
    "is_instructor_available",
    # Mixin
    "InstructorMixin",
]
