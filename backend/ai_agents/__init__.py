"""Memory strategy base class."""

from abc import ABC, abstractmethod


class MemoryStrategy(ABC):
    """Base class for conversation memory strategies."""
    
    @abstractmethod
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        max_tokens: int = None,
    ) -> list[dict]:
        """
        Build context from message history.
        
        Args:
            messages: Full message history from DB
            system_prompt: Optional system prompt to prepend
            max_tokens: Max tokens for context (provider-specific)
            
        Returns:
            Messages list ready for LLM
        """
        ...
