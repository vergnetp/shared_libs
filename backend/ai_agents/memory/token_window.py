"""Token-limited memory strategy."""

from .base import MemoryStrategy


class TokenWindowMemory(MemoryStrategy):
    """Fit as many recent messages as possible within token budget."""
    
    def __init__(self, max_tokens: int = 100000, reserve_output: int = 4096):
        self.max_tokens = max_tokens
        self.reserve_output = reserve_output
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        max_tokens: int = None,
    ) -> list[dict]:
        budget = (max_tokens or self.max_tokens) - self.reserve_output
        
        result = []
        used_tokens = 0
        
        # System prompt first
        if system_prompt:
            system_tokens = self._estimate_tokens(system_prompt)
            if system_tokens < budget:
                result.append({"role": "system", "content": system_prompt})
                used_tokens += system_tokens
        
        # Add messages from newest to oldest, then reverse
        selected = []
        for m in reversed(messages):
            msg_tokens = self._estimate_tokens(m["content"])
            if used_tokens + msg_tokens > budget:
                break
            selected.append({"role": m["role"], "content": m["content"]})
            used_tokens += msg_tokens
        
        # Reverse to chronological order
        result.extend(reversed(selected))
        
        return result
    
    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token."""
        return len(text) // 4 + 1
