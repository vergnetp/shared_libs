"""First + Last messages memory strategy."""

from .base import MemoryStrategy


class FirstLastMemory(MemoryStrategy):
    """Keep first K messages + last N messages."""
    
    def __init__(self, first: int = 2, last: int = 10):
        self.first = first
        self.last = last
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        max_tokens: int = None,
    ) -> list[dict]:
        result = []
        
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        
        if len(messages) <= self.first + self.last:
            # Not enough to split, use all
            for m in messages:
                result.append({"role": m["role"], "content": m["content"]})
        else:
            # First K
            for m in messages[:self.first]:
                result.append({"role": m["role"], "content": m["content"]})
            
            # Marker
            result.append({
                "role": "system",
                "content": f"[... {len(messages) - self.first - self.last} messages omitted ...]"
            })
            
            # Last N
            for m in messages[-self.last:]:
                result.append({"role": m["role"], "content": m["content"]})
        
        return result
