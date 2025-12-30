"""
Memory management workers.

Task processors for:
- Thread summarization
- Memory compaction

CRITICAL:
1. Each job gets fresh DB connection via context manager
2. Workers verify resource scope before processing
3. Connections are properly closed on success/failure
"""

from typing import Dict, Any
from datetime import datetime

from backend.app_kernel import get_logger, get_metrics
from backend.app_kernel.jobs import JobContext

from ..jobs import AgentJobContext
from ..authz import CurrentUser, verify_resource_scope, ScopeError


def _get_logger():
    """Get logger (lazy to avoid import issues)."""
    return get_logger()


def _get_metrics():
    """Get metrics (lazy to avoid import issues)."""
    return get_metrics()


# =============================================================================
# Thread Summarization
# =============================================================================

async def summarize_thread(payload: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    """
    Summarize thread messages.
    
    Payload:
        thread_id: Thread to summarize
        max_messages: Optional max messages to include
    """
    from ..deps import get_db_context
    from ...config import get_settings
    import json
    
    logger = _get_logger()
    metrics = _get_metrics()
    
    app_ctx = AgentJobContext.from_kernel_context(ctx)
    thread_id = payload["thread_id"]
    max_messages = payload.get("max_messages", 100)
    
    logger.info(
        f"Summarizing thread: {thread_id}",
        extra={
            "thread_id": thread_id,
            "user_id": app_ctx.user_id,
            "job_id": ctx.job_id,
        }
    )
    
    user = CurrentUser(id=app_ctx.user_id, role="user")
    settings = get_settings()
    
    async with get_db_context() as db:
        try:
            # SCOPE VERIFY: Check thread belongs to user/workspace
            thread = await verify_resource_scope(
                db, user, "threads", thread_id,
                expected_workspace_id=app_ctx.workspace_id
            )
            
            # Load messages
            messages = await db.find_entities(
                "messages",
                where_clause="[thread_id] = ?",
                params=(thread_id,),
                order_by="created_at ASC",
                limit=max_messages,
            )
            
            if not messages:
                return {"thread_id": thread_id, "summary": "", "message_count": 0}
            
            # Build conversation text
            conversation = []
            for msg in messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                conversation.append(f"{role}: {content}")
            
            full_text = "\n\n".join(conversation)
            
            # Use LLM to summarize
            try:
                from anthropic import AsyncAnthropic
                
                client = AsyncAnthropic(api_key=settings.anthropic_api_key)
                response = await client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=500,
                    system="Summarize the following conversation concisely, capturing the key points, decisions, and context. Be brief but comprehensive.",
                    messages=[{"role": "user", "content": full_text}],
                )
                summary = response.content[0].text
            except Exception as e:
                logger.warning(f"LLM summarization failed: {e}, using simple truncation")
                summary = full_text[:500] + "..." if len(full_text) > 500 else full_text
            
            # Update thread metadata with summary
            meta = thread.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            
            meta["summary"] = summary
            meta["summary_message_count"] = len(messages)
            
            await db.save_entity("threads", {
                **thread,
                "metadata": meta,
            })
            
            metrics.increment("threads_summarized")
            
            return {
                "thread_id": thread_id,
                "summary": summary,
                "message_count": len(messages),
            }
            
        except ScopeError as e:
            metrics.increment("errors", endpoint="summarization", error_type="scope_error")
            raise PermissionError(str(e))
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            metrics.increment("errors", endpoint="summarization", error_type=type(e).__name__)
            raise


# =============================================================================
# Memory Compaction
# =============================================================================

async def compact_memory(payload: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    """
    Compact old messages by summarizing and archiving.
    
    Payload:
        thread_id: Thread to compact
        keep_recent: Number of recent messages to keep (default: 20)
    """
    from ..deps import get_db_context
    from ...config import get_settings
    import json
    
    logger = _get_logger()
    metrics = _get_metrics()
    
    app_ctx = AgentJobContext.from_kernel_context(ctx)
    thread_id = payload["thread_id"]
    keep_recent = payload.get("keep_recent", 20)
    settings = get_settings()
    
    logger.info(
        f"Compacting thread: {thread_id}",
        extra={
            "thread_id": thread_id,
            "keep_recent": keep_recent,
            "job_id": ctx.job_id,
        }
    )
    
    user = CurrentUser(id=app_ctx.user_id, role="user")
    
    async with get_db_context() as db:
        try:
            # SCOPE VERIFY: Check thread belongs to user/workspace
            thread = await verify_resource_scope(
                db, user, "threads", thread_id,
                expected_workspace_id=app_ctx.workspace_id
            )
            
            # Get all messages, sorted by created_at
            all_messages = await db.find_entities(
                "messages",
                where_clause="[thread_id] = ? AND [deleted_at] IS NULL",
                params=(thread_id,),
                order_by="created_at ASC",
            )
            
            if len(all_messages) <= keep_recent:
                return {
                    "thread_id": thread_id,
                    "compacted": 0,
                    "kept": len(all_messages),
                }
            
            # Split into old and recent
            old_messages = all_messages[:-keep_recent]
            recent_messages = all_messages[-keep_recent:]
            
            # Summarize old messages
            conversation = []
            for msg in old_messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                conversation.append(f"{role}: {content}")
            
            full_text = "\n\n".join(conversation)
            
            # Use LLM to summarize
            try:
                from anthropic import AsyncAnthropic
                
                client = AsyncAnthropic(api_key=settings.anthropic_api_key)
                response = await client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=1000,
                    system="Create a detailed summary of this conversation history that preserves important context, decisions, and information that would be needed to continue the conversation.",
                    messages=[{"role": "user", "content": full_text}],
                )
                summary = response.content[0].text
            except Exception as e:
                logger.warning(f"LLM summarization failed: {e}, using simple truncation")
                summary = full_text[:1000] + "..." if len(full_text) > 1000 else full_text
            
            # Store summary in thread metadata
            meta = thread.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            
            meta["compacted_summary"] = summary
            meta["compacted_count"] = len(old_messages)
            meta["compacted_at"] = datetime.utcnow().isoformat()
            
            await db.save_entity("threads", {
                **thread,
                "metadata": meta,
            })
            
            # Soft-delete old messages
            now = datetime.utcnow().isoformat()
            for msg in old_messages:
                msg["deleted_at"] = now
                await db.save_entity("messages", msg)
            
            metrics.increment("threads_compacted")
            
            return {
                "thread_id": thread_id,
                "compacted": len(old_messages),
                "kept": len(recent_messages),
                "summary_length": len(summary),
            }
            
        except ScopeError as e:
            metrics.increment("errors", endpoint="compaction", error_type="scope_error")
            raise PermissionError(str(e))
        except Exception as e:
            logger.error(f"Compaction failed: {e}")
            metrics.increment("errors", endpoint="compaction", error_type=type(e).__name__)
            raise
