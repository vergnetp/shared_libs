"""Context builder base class."""

from abc import ABC, abstractmethod


class ContextBuilder(ABC):
    """Builds context for LLM from various sources."""
    
    @abstractmethod
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        tools: list[dict] = None,
        documents: list[dict] = None,
        **kwargs,
    ) -> list[dict]:
        """
        Build context for LLM.
        
        Args:
            messages: Conversation messages
            system_prompt: System prompt
            tools: Tool definitions
            documents: RAG documents to include
            **kwargs: Additional context sources
            
        Returns:
            Messages ready for LLM
        """
        ...
