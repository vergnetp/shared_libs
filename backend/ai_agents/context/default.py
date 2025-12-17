"""Default context builder."""

from .base import ContextBuilder
from ..memory import MemoryStrategy


class DefaultContextBuilder(ContextBuilder):
    """
    Default context builder.
    
    Combines:
    - System prompt
    - RAG documents (if any)
    - Conversation history (via memory strategy)
    """
    
    def __init__(self, memory: MemoryStrategy):
        self.memory = memory
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        tools: list[dict] = None,
        documents: list[dict] = None,
        **kwargs,
    ) -> list[dict]:
        # Build system prompt with documents
        full_system = self._build_system_prompt(system_prompt, documents)
        
        # Apply memory strategy
        context = await self.memory.build(
            messages,
            system_prompt=full_system,
            **kwargs,
        )
        
        return context
    
    def _build_system_prompt(
        self,
        base_prompt: str,
        documents: list[dict] = None,
    ) -> str:
        parts = []
        
        if base_prompt:
            parts.append(base_prompt)
        
        if documents:
            parts.append("\n\n## Relevant Documents\n")
            for doc in documents:
                title = doc.get("title", "Document")
                content = doc.get("content", "")
                parts.append(f"### {title}\n{content}\n")
        
        return "\n".join(parts) if parts else None
