from __future__ import annotations
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
    
    # Alias for run with system prompt support
    async def complete(
        self,
        messages: list[dict],
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        """
        Alias for run() with system prompt support.
        
        Args:
            messages: List of messages
            system: Optional system prompt (prepended as system message)
            temperature: Sampling temperature
            max_tokens: Max tokens
            tools: Tool definitions
        """
        # Prepend system message if provided
        if system:
            messages = [{"role": "system", "content": system}] + list(messages)
        
        return await self.run(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            **kwargs,
        )
    
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
