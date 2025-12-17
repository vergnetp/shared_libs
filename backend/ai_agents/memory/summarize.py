"""Summarization-based memory strategy."""

from .base import MemoryStrategy


class SummarizeMemory(MemoryStrategy):
    """
    Use summaries for old messages, full recent messages.
    
    Expects thread to have summaries stored separately.
    """
    
    def __init__(self, recent: int = 10):
        self.recent = recent
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        max_tokens: int = None,
        summaries: list[dict] = None,  # From thread_summaries table
    ) -> list[dict]:
        result = []
        
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        
        # Add summaries of older conversation
        if summaries:
            summary_text = "\n\n".join(s["summary"] for s in summaries)
            result.append({
                "role": "system",
                "content": f"Summary of earlier conversation:\n{summary_text}"
            })
        
        # Add recent messages
        recent_messages = messages[-self.recent:] if len(messages) > self.recent else messages
        
        for m in recent_messages:
            result.append({"role": m["role"], "content": m["content"]})
        
        return result
