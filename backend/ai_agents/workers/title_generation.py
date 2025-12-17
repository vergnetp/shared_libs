"""Thread title generation worker."""

from shared_lib.processing import process
from shared_lib.logging import info


async def queue_title_generation(thread_id: str):
    """Queue thread for title generation."""
    await process(
        entity={"thread_id": thread_id},
        processor="ai_agents.workers.title_generation:generate_title",
        queue_name="ai_title_generation",
    )


async def generate_title(entity: dict, conn, provider):
    """
    Generate a title for a thread based on its content.
    
    Args:
        entity: {"thread_id": str}
        conn: Database connection
        provider: LLM provider
    """
    thread_id = entity["thread_id"]
    
    # Check if already has title
    thread = await conn.get_entity("threads", thread_id)
    if thread.get("title"):
        info("Thread already has title", thread_id=thread_id)
        return
    
    # Get first few messages
    messages = await conn.find_entities(
        "messages",
        where_clause="[thread_id] = ?",
        params=(thread_id,),
        order_by="created_at ASC",
        limit=6,
    )
    
    if not messages:
        return
    
    # Build prompt
    prompt = """Generate a short, descriptive title (max 50 characters) for this conversation. 
Return ONLY the title, no quotes or extra text.

Conversation:
"""
    for m in messages:
        role = m["role"].upper()
        content = m["content"][:200]  # Truncate long messages
        prompt += f"{role}: {content}\n"
    
    # Generate title
    response = await provider.run(
        [{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=50,
    )
    
    title = response.content.strip()[:50]
    
    # Update thread
    thread["title"] = title
    await conn.save_entity("threads", thread)
    
    info("Title generated", thread_id=thread_id, title=title)
