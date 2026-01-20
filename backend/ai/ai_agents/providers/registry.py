from __future__ import annotations
"""Provider registry."""

from typing import Type
from .base import LLMProvider


_providers: dict[str, Type[LLMProvider]] = {}


def register_provider(name: str, provider_class: Type[LLMProvider]):
    """Register a provider class."""
    _providers[name] = provider_class


def get_provider(name: str, **kwargs) -> LLMProvider:
    """
    Get a provider instance.
    
    Args:
        name: Provider name (anthropic, openai, openai_assistant, ollama)
        **kwargs: Provider-specific args (api_key, model, etc.)
    
    Returns:
        Configured provider instance
    """
    if name not in _providers:
        raise ValueError(f"Unknown provider: {name}. Available: {list(_providers.keys())}")
    
    return _providers[name](**kwargs)


def list_providers() -> list[str]:
    """List registered provider names."""
    return list(_providers.keys())


# Register built-in providers
def _register_builtins():
    from .anthropic import AnthropicProvider
    from .openai import OpenAIProvider, OpenAIAssistantProvider
    from .ollama import OllamaProvider
    from .groq import GroqProvider
    from .tinyroberta import TinyRobertaProvider
    
    register_provider("anthropic", AnthropicProvider)
    register_provider("openai", OpenAIProvider)
    register_provider("openai_assistant", OpenAIAssistantProvider)
    register_provider("ollama", OllamaProvider)
    register_provider("groq", GroqProvider)
    register_provider("tinyroberta", TinyRobertaProvider)


_register_builtins()
