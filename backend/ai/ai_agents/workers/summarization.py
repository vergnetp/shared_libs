"""Thread summarization worker.

Updates the rolling summary on a thread when unsummarized messages
exceed a threshold. Called from chat endpoint via fire-and-forget.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from ....job_queue import process
from ....log import info, error

from ..memory.summarize import SummarizationHelper
from ..model_config import get_model_info


async def queue_summarization(
    thread_id: str,
    max_context: int = 128000,
    system_chars: int = 0,
    tools_chars: int = 0,
) -> None:
    """
    Queue thread for background summarization.
    
    Called from chat endpoint after saving assistant response.
    Fire-and-forget - doesn't block the chat response.
    
    Args:
        thread_id: Thread to summarize
        max_context: Model's context limit (for word limit calc)
        system_chars: Size of system prompt
        tools_chars: Size of tools JSON
    """
    await process(
        entity={
            "thread_id": thread_id,
            "max_context": max_context,
            "system_chars": system_chars,
            "tools_chars": tools_chars,
        },
        processor="ai_agents.workers.summarization:summarize_thread",
        queue_name="ai_summarization",
    )


async def maybe_queue_summarization(
    thread_id: str,
    thread: dict,
    messages: list[dict],
    threshold_chars: int = 16000,
    max_context: int = 128000,
    system_chars: int = 0,
    tools_chars: int = 0,
) -> bool:
    """
    Check if summarization needed and queue if so.
    
    Returns True if queued, False otherwise.
    """
    # Calculate unsummarized chars
    summarized_until = thread.get("summarized_until_msg_id")
    unsummarized_chars = SummarizationHelper.calculate_unsummarized_chars(
        messages,
        summarized_until,
    )
    
    if unsummarized_chars > threshold_chars:
        # Fire-and-forget
        asyncio.create_task(queue_summarization(
            thread_id=thread_id,
            max_context=max_context,
            system_chars=system_chars,
            tools_chars=tools_chars,
        ))
        return True
    
    return False


async def summarize_thread(
    entity: dict,
    conn: Any,
    provider: Any,
) -> dict:
    """
    Update rolling summary for a thread.
    
    Called by job queue processor.
    
    Args:
        entity: {"thread_id": str, "max_context": int, ...}
        conn: Database connection
        provider: LLM provider for summarization
    """
    thread_id = entity["thread_id"]
    max_context = entity.get("max_context", 128000)
    system_chars = entity.get("system_chars", 0)
    tools_chars = entity.get("tools_chars", 0)
    
    info("Starting summarization", thread_id=thread_id)
    
    try:
        # Get thread
        thread = await conn.get_entity("threads", thread_id)
        if not thread:
            error("Thread not found", thread_id=thread_id)
            return {"error": "Thread not found"}
        
        existing_summary = thread.get("summary", "")
        summarized_until = thread.get("summarized_until_msg_id")
        
        # Get unsummarized messages
        from ..store.messages import MessageStore
        messages_store = MessageStore(conn)
        
        to_summarize = await messages_store.get_unsummarized(
            thread_id=thread_id,
            after_msg_id=summarized_until,
            keep_recent=10,  # Keep last 10 for detail
        )
        
        if not to_summarize:
            info("No messages to summarize", thread_id=thread_id)
            return {"skipped": "No messages to summarize"}
        
        # Calculate word limit based on context budget
        # Rough estimate: summary gets ~1/4 of available after fixed costs
        available = max_context - (system_chars // 4) - (tools_chars // 4) - 4000
        word_limit = min(500, max(100, available // 8))
        
        # Build prompt
        prompt = SummarizationHelper.build_summarization_prompt(
            existing_summary=existing_summary,
            new_messages=to_summarize,
            word_limit=word_limit,
        )
        
        # Generate summary
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
        )
        
        new_summary = response.content.strip()
        last_msg_id = to_summarize[-1]["id"]
        
        # Update thread
        thread["summary"] = new_summary
        thread["summarized_until_msg_id"] = last_msg_id
        await conn.save_entity("threads", thread)
        
        info("Summarization complete",
             thread_id=thread_id,
             messages_summarized=len(to_summarize),
             summary_length=len(new_summary))
        
        return {
            "success": True,
            "messages_summarized": len(to_summarize),
            "summary_length": len(new_summary),
        }
        
    except Exception as e:
        error("Summarization failed", thread_id=thread_id, error=str(e))
        return {"error": str(e)}


async def force_summarize(
    thread_id: str,
    conn: Any,
    provider: Any,
    max_context: int = 128000,
) -> dict:
    """
    Force immediate summarization (not queued).
    
    Useful for testing or manual triggers.
    """
    return await summarize_thread(
        entity={
            "thread_id": thread_id,
            "max_context": max_context,
            "system_chars": 0,
            "tools_chars": 0,
        },
        conn=conn,
        provider=provider,
    )
