from __future__ import annotations
"""Token-limited memory strategy."""

from typing import Callable, Optional

from .base import MemoryStrategy


def estimate_tokens(text: str) -> int:
    """
    Heuristic token estimation.
    
    For accurate counting, use:
        from embeddings import count_tokens
        memory = TokenWindowMemory(count_tokens_fn=lambda t: count_tokens(t, "gpt-4"))
    """
    if not text:
        return 0
    
    # Count CJK characters (Chinese, Japanese, Korean)
    cjk_count = 0
    for c in text:
        cp = ord(c)
        if (0x4E00 <= cp <= 0x9FFF or    # CJK Unified Ideographs
            0x3400 <= cp <= 0x4DBF or    # CJK Extension A
            0x3040 <= cp <= 0x30FF or    # Hiragana + Katakana
            0xAC00 <= cp <= 0xD7AF):     # Korean Hangul
            cjk_count += 1
    
    latin_count = len(text) - cjk_count
    cjk_tokens = cjk_count * 0.7
    latin_tokens = latin_count / 3.5
    
    return max(1, int(cjk_tokens + latin_tokens))


def create_token_counter(llm_model: str = None) -> Callable[[str], int]:
    """
    Create a token counter for a specific LLM.
    
    Args:
        llm_model: LLM model (e.g., "gpt-4", "claude-sonnet")
                   If None, returns heuristic counter
    """
    if llm_model is None:
        return estimate_tokens
    
    try:
        from ...embeddings import count_tokens
        return lambda text: count_tokens(text, model=llm_model)
    except ImportError:
        return estimate_tokens


class TokenWindowMemory(MemoryStrategy):
    """Fit as many recent messages as possible within token budget."""
    
    def __init__(
        self, 
        max_tokens: int = 100000, 
        reserve_output: int = 4096,
        llm_model: str = None,
        count_tokens_fn: Callable[[str], int] = None,
        **kwargs,  # Accept extra params from other strategies
    ):
        """
        Args:
            max_tokens: Maximum context tokens
            reserve_output: Tokens to reserve for model output
            llm_model: LLM model for accurate counting (e.g., "gpt-4")
            count_tokens_fn: Custom token counter (overrides llm_model)
        """
        self.max_tokens = max_tokens
        self.reserve_output = reserve_output
        
        # Priority: count_tokens_fn > llm_model > heuristic
        if count_tokens_fn is not None:
            self.count_tokens = count_tokens_fn
        else:
            self.count_tokens = create_token_counter(llm_model)
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        max_tokens: int = None,
        **kwargs,  # Accept extra params from context builder
    ) -> list[dict]:
        budget = (max_tokens or self.max_tokens) - self.reserve_output
        
        result = []
        used_tokens = 0
        
        # System prompt first
        if system_prompt:
            system_tokens = self.count_tokens(system_prompt)
            if system_tokens < budget:
                result.append({"role": "system", "content": system_prompt})
                used_tokens += system_tokens
        
        # Add messages from newest to oldest, then reverse
        selected = []
        for m in reversed(messages):
            msg_tokens = self.count_tokens(m["content"])
            if used_tokens + msg_tokens > budget:
                break
            selected.append({"role": m["role"], "content": m["content"]})
            used_tokens += msg_tokens
        
        # Reverse to chronological order
        result.extend(reversed(selected))
        
        return result
