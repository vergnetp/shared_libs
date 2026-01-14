"""LLM providers."""

from .base import LLMProvider
from .anthropic import AnthropicProvider
from .openai import OpenAIProvider, OpenAIAssistantProvider
from .ollama import OllamaProvider
from .groq import GroqProvider
from .cascading import CascadingProvider
from .tinyroberta import TinyRobertaProvider
from .registry import get_provider, register_provider, list_providers

# Instructor support (optional - requires `pip install instructor`)
from .instructor_support import (
    enable_instructor,
    is_instructor_available,
    extract_tool_calls,
    ToolCallModel,
    ToolCallList,
    StructuredResponse,
    TextResponse,
)

__all__ = [
    # Providers
    "LLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "OpenAIAssistantProvider",
    "OllamaProvider",
    "GroqProvider",
    "CascadingProvider",
    "TinyRobertaProvider",
    "get_provider",
    "register_provider",
    "list_providers",
    # Instructor support
    "enable_instructor",
    "is_instructor_available",
    "extract_tool_calls",
    "ToolCallModel",
    "ToolCallList",
    "StructuredResponse",
    "TextResponse",
]
