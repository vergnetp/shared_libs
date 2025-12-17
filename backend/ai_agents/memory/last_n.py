"""Last N messages memory strategy."""

from .base import MemoryStrategy


class LastNMemory(MemoryStrategy):
    """Keep last N messages."""
    
    def __init__(self, n: int = 20):
        self.n = n
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        max_tokens: int = None,
    ) -> list[dict]:
        result = []
        
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        
        # Take last N messages
        recent = messages[-self.n:] if len(messages) > self.n else messages
        
        for m in recent:
            result.append({
                "role": m["role"],
                "content": m["content"],
            })
        
        return result
