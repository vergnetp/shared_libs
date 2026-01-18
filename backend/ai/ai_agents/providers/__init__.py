"""
LLM providers.

Refactored to use cloud.llm HTTP clients where possible:
- AnthropicProvider → cloud.llm.AsyncAnthropicClient
- OpenAIProvider → cloud.llm.AsyncOpenAICompatClient  
- GroqProvider → cloud.llm.AsyncOpenAICompatClient (Groq base URL)

SDK-based (complex state management):
- OpenAIAssistantProvider → openai SDK (threads, runs, polling)
- OllamaProvider → httpx directly (local server)
"""

from .base import LLMProvider
from .anthropic import AnthropicProvider
from .openai import OpenAIProvider
from .ollama import OllamaProvider
from .groq import GroqProvider
from .cascading import CascadingProvider
from .tinyroberta import TinyRobertaProvider
from .registry import get_provider, register_provider, list_providers

# OpenAIAssistantProvider requires the openai SDK - import lazily
try:
    from ._openai_assistant import OpenAIAssistantProvider
except ImportError:
    OpenAIAssistantProvider = None  # SDK not installed

__all__ = [
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
]
