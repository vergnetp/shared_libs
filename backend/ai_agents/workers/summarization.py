"""Thread summarization worker."""

from shared_lib.processing import process
from shared_lib.logging import info


async def queue_summarization(thread_id: str, force: bool = False):
    """
    Queue thread for background summarization.
    
    Args:
        thread_id: Thread to summarize
        force: Force re-summarization even if recently done
    """
    await process(
        entity={"thread_id": thread_id, "force": force},
        processor="ai_agents.workers.summarization:summarize_thread",
        queue_name="ai_summarization",
    )


async def summarize_thread(entity: dict, conn, provider):
    """
    Summarize older messages in a thread.
    
    Called by processing framework.
    
    Args:
        entity: {"thread_id": str, "force": bool}
        conn: Database connection
        provider: LLM provider
    """
    thread_id = entity["thread_id"]
    force = entity.get("force", False)
    
    info("Summarizing thread", thread_id=thread_id, force=force)
    
    # Get unsummarized messages
    messages = await conn.find_entities(
        "messages",
        where_clause="[thread_id] = ? AND ([summarized] IS NULL OR [summarized] = ?)",
        params=(thread_id, False),
        order_by="created_at ASC",
    )
    
    if len(messages) < 20 and not force:
        info("Not enough messages to summarize", count=len(messages))
        return
    
    # Keep last 10 messages intact
    to_summarize = messages[:-10] if len(messages) > 10 else []
    
    if not to_summarize:
        return
    
    # Build summary prompt
    prompt = "Summarize this conversation concisely, preserving key information:\n\n"
    for m in to_summarize:
        prompt += f"{m['role'].upper()}: {m['content']}\n\n"
    
    # Generate summary
    response = await provider.run([{"role": "user", "content": prompt}])
    
    # Save summary
    await conn.save_entity("thread_summaries", {
        "thread_id": thread_id,
        "summary": response.content,
        "message_count": len(to_summarize),
        "first_message_id": to_summarize[0]["id"],
        "last_message_id": to_summarize[-1]["id"],
    })
    
    # Mark messages as summarized
    for m in to_summarize:
        m["summarized"] = True
        await conn.save_entity("messages", m)
    
    info("Summarization complete", 
         thread_id=thread_id, 
         messages_summarized=len(to_summarize))
