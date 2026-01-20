"""Rolling summary memory strategy.

Uses a single rolling summary + recent messages for context.
Summary is stored on the thread and updated incrementally.
"""
from __future__ import annotations

from typing import Optional, Any
from .base import MemoryStrategy


class SummarizeMemory(MemoryStrategy):
    """
    Rolling summary + recent messages.
    
    Context structure:
    - [system prompt]
    - [rolling summary of older conversation]
    - [last N chars of recent messages - full detail]
    - [current user input]
    
    The summary is stored on the thread and updated when unsummarized
    messages exceed a threshold. Updates happen in the background via
    a job queue, so they don't block chat responses.
    
    Args:
        recent_chars: Max characters for recent messages (default 8000 ≈ 2k tokens)
        summarize_threshold_chars: Queue summarization when unsummarized exceeds this
        summary_chars_min: Minimum characters to allocate for summary
        summary_chars_max: Maximum characters to allocate for summary (safety cap)
    """
    
    def __init__(
        self,
        recent_chars: int = 8000,
        summarize_threshold_chars: int = 16000,
        summary_chars_min: int = 500,
        summary_chars_max: int = 8000,
        **kwargs,  # Accept extra params like 'n' from other strategies
    ):
        self.recent_chars = recent_chars
        self.summarize_threshold_chars = summarize_threshold_chars
        self.summary_chars_min = summary_chars_min
        self.summary_chars_max = summary_chars_max
    
    async def build(
        self,
        messages: list[dict],
        system_prompt: str = None,
        max_tokens: int = None,
        # Additional context for budget calculation
        tools_chars: int = 0,
        user_input_chars: int = 0,
        reserve_output_tokens: int = 4000,
        # Thread info for summary
        thread_summary: str = None,
        **kwargs,
    ) -> list[dict]:
        """
        Build context with summary + recent messages.
        
        Args:
            messages: Recent messages (already filtered by chars in caller)
            system_prompt: System prompt (already includes user_context)
            max_tokens: Model's max context (from model_config)
            tools_chars: Size of tools JSON
            user_input_chars: Size of current user message
            reserve_output_tokens: Tokens to reserve for response
            thread_summary: Rolling summary from thread.summary field
        """
        result = []
        
        # Calculate available budget for summary
        summary_budget = self._calculate_summary_budget(
            max_tokens=max_tokens or 128000,
            system_chars=len(system_prompt or ""),
            tools_chars=tools_chars,
            recent_chars=sum(len(m.get("content") or "") for m in messages),
            user_input_chars=user_input_chars,
            reserve_output_tokens=reserve_output_tokens,
        )
        
        # Build system prompt with summary
        if system_prompt or thread_summary:
            full_system = self._build_system_with_summary(
                system_prompt,
                thread_summary,
                summary_budget,
            )
            result.append({"role": "system", "content": full_system})
        
        # Add recent messages (already normalized, tool_calls stripped)
        for m in messages:
            msg = {"role": m["role"], "content": m.get("content") or ""}
            result.append(msg)
        
        return result
    
    def _calculate_summary_budget(
        self,
        max_tokens: int,
        system_chars: int,
        tools_chars: int,
        recent_chars: int,
        user_input_chars: int,
        reserve_output_tokens: int,
    ) -> int:
        """Calculate how many characters available for summary."""
        # Convert chars to tokens (rough: 4 chars ≈ 1 token)
        fixed_tokens = (system_chars + tools_chars + recent_chars + user_input_chars) // 4
        
        available_tokens = max_tokens - fixed_tokens - reserve_output_tokens
        available_chars = available_tokens * 4
        
        # Clamp to min/max
        return max(
            self.summary_chars_min,
            min(available_chars, self.summary_chars_max)
        )
    
    def _build_system_with_summary(
        self,
        system_prompt: str,
        summary: str,
        budget_chars: int,
    ) -> str:
        """Combine system prompt with summary."""
        parts = []
        
        if system_prompt:
            parts.append(system_prompt)
        
        if summary:
            # Truncate summary if over budget
            if len(summary) > budget_chars:
                summary = summary[:budget_chars - 3] + "..."
            
            parts.append(f"\n\n## Conversation Summary\n{summary}")
        
        return "\n".join(parts)
    
    def should_summarize(
        self,
        unsummarized_chars: int,
    ) -> bool:
        """Check if summarization should be triggered."""
        return unsummarized_chars > self.summarize_threshold_chars
    
    def get_summary_word_limit(
        self,
        max_tokens: int,
        system_chars: int,
        tools_chars: int,
        reserve_output_tokens: int = 4000,
    ) -> int:
        """
        Calculate word limit for summary generation prompt.
        
        Used by summarization worker to tell the LLM how long the summary should be.
        """
        # Budget for summary in tokens
        fixed_tokens = (system_chars + tools_chars + self.recent_chars) // 4
        available_tokens = max_tokens - fixed_tokens - reserve_output_tokens
        
        # Clamp
        summary_tokens = max(100, min(available_tokens // 2, 2000))
        
        # Tokens to words (rough: 1 token ≈ 0.75 words)
        return int(summary_tokens * 0.75)


class SummarizationHelper:
    """
    Helper for summarization operations.
    
    Used by the summarization worker to generate rolling summaries.
    """
    
    @staticmethod
    def build_summarization_prompt(
        existing_summary: str,
        new_messages: list[dict],
        word_limit: int,
    ) -> str:
        """Build prompt for incremental summary update."""
        # Format new messages
        conversation = ""
        for m in new_messages:
            role = m.get("role", "").upper()
            content = m.get("content", "")
            conversation += f"{role}: {content}\n\n"
        
        if existing_summary:
            return f"""Update this conversation summary with new messages.
Keep it under {word_limit} words - be concise.
Write in the same language as the conversation.

Previous summary:
{existing_summary}

New messages:
{conversation}

Updated summary:"""
        else:
            return f"""Summarize this conversation concisely.
Keep it under {word_limit} words.
Write in the same language as the conversation.

Conversation:
{conversation}

Summary:"""
    
    @staticmethod
    def calculate_unsummarized_chars(
        messages: list[dict],
        summarized_until_msg_id: str = None,
    ) -> int:
        """Calculate total characters of unsummarized messages."""
        if not summarized_until_msg_id:
            return sum(len(m.get("content") or "") for m in messages)
        
        # Find the cutoff point
        found = False
        total = 0
        for m in messages:
            if found:
                total += len(m.get("content") or "")
            elif m.get("id") == summarized_until_msg_id:
                found = True
        
        return total
