from __future__ import annotations
"""Last N messages memory strategy."""

import json
from .base import MemoryStrategy


class LastNMemory(MemoryStrategy):
    """
    Keep last N exchanges (user-assistant pairs).
    
    This counts exchanges, not raw messages. With tool calls, a single
    exchange can span 4+ messages (user → assistant_tool → tool → assistant).
    Setting n=10 means 10 user messages + their responses.
    """
    
    def __init__(self, n: int = 20, **kwargs):
        self.n = n
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        max_tokens: int = None,
        **kwargs,  # Accept extra params from context builder
    ) -> list[dict]:
        result = []
        
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        
        # Count user messages to determine exchanges
        # Find the index where we have at most N user messages
        user_count = 0
        start_idx = len(messages)
        
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                user_count += 1
                if user_count > self.n:
                    break
                start_idx = i
        
        # Take messages from start_idx to end
        recent = messages[start_idx:]
        
        print(f"[DEBUG LastNMemory] Processing {len(recent)} messages")
        
        # Messages are already normalized (tool_calls stripped for LLM compatibility)
        for m in recent:
            msg = {
                "role": m.get("role"),
                "content": m.get("content") or "",
            }
            result.append(msg)
        
        return result
