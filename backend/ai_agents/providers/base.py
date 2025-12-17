"""Base LLM provider interface."""

from abc import ABC, abstractmethod
from typing import AsyncIterator

from ..core import ProviderResponse, Message


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""
    
    name: str = "base"
    
    @abstractmethod
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        """
        Run completion.
        
        Args:
            messages: List of {"role": str, "content": str} dicts
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            tools: Tool definitions for function calling
            **kwargs: Provider-specific options
            
        Returns:
            ProviderResponse with content and metadata
        """
        ...
    
    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream completion tokens.
        
        Yields:
            Content chunks as they arrive
        """
        ...
    
    @abstractmethod
    def count_tokens(self, messages: list[dict]) -> int:
        """
        Count tokens in messages.
        
        Args:
            messages: Messages to count
            
        Returns:
            Token count
        """
        ...
    
    @property
    @abstractmethod
    def max_context_tokens(self) -> int:
        """Max context window for this model."""
        ...
