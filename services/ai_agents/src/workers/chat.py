"""
Chat processing worker.

Processes chat messages off the request path with:
- Full LLM call handling
- Redis pub/sub for streaming chunks
- Proper scope verification
- Per-job DB connection lifecycle

CRITICAL:
1. Each job gets fresh DB connection via context manager
2. Workers verify resource (thread) scope before processing
3. Connections are properly closed on success/failure
"""

import os
import json
from typing import Dict, Any, Optional
from datetime import datetime
import uuid

from backend.app_kernel import get_logger, get_metrics
from backend.app_kernel.jobs import JobContext

from ..jobs import AgentJobContext
from ..authz import CurrentUser, verify_resource_scope, ScopeError

# Redis pub/sub for streaming
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


def _get_logger():
    """Get logger (lazy to avoid import issues)."""
    return get_logger()


def _get_metrics():
    """Get metrics (lazy to avoid import issues)."""
    return get_metrics()


# =============================================================================
# Redis Helper
# =============================================================================

_redis_client: Optional["redis.Redis"] = None


async def _get_redis() -> Optional["redis.Redis"]:
    """Get Redis connection for pub/sub."""
    global _redis_client
    
    redis_url = os.getenv("REDIS_URL")
    if not REDIS_AVAILABLE or not redis_url:
        return None
    
    if _redis_client is None:
        _redis_client = redis.from_url(redis_url)
    
    return _redis_client


async def _publish_error(channel: str, error: str):
    """Publish error to stream channel."""
    redis_client = await _get_redis()
    if redis_client:
        try:
            await redis_client.publish(
                channel,
                json.dumps({"type": "error", "error": error})
            )
        except:
            pass


# =============================================================================
# Chat Processor
# =============================================================================

async def process_chat(payload: Dict[str, Any], ctx: JobContext) -> Dict[str, Any]:
    """
    Process chat message.
    
    Payload:
        thread_id: Thread ID
        message: User message text
        message_id: ID of saved user message
        stream: Whether to publish streaming chunks
        options: Chat options (temperature, etc.)
    """
    from ..deps import get_db_context, Agent
    from ...config import get_settings
    
    logger = _get_logger()
    metrics = _get_metrics()
    
    app_ctx = AgentJobContext.from_kernel_context(ctx)
    thread_id = payload["thread_id"]
    message = payload["message"]
    stream = payload.get("stream", False)
    options = payload.get("options", {})
    
    logger.info(
        f"Processing chat: {thread_id}",
        extra={
            "thread_id": thread_id,
            "user_id": app_ctx.user_id,
            "stream": stream,
            "job_id": ctx.job_id,
        }
    )
    
    user = CurrentUser(id=app_ctx.user_id, role="user")
    
    # Use fresh connection for this job
    async with get_db_context() as db:
        try:
            # SCOPE VERIFY: Load and check thread belongs to user's workspace
            thread = await verify_resource_scope(
                db, user, "threads", thread_id,
                expected_workspace_id=app_ctx.workspace_id
            )
            
            # Get agent
            agent_data = await db.get_entity("agents", thread["agent_id"])
            if not agent_data:
                raise ValueError(f"Agent not found: {thread['agent_id']}")
            
            # Get settings
            settings = get_settings()
            
            def _get_api_key(provider: str) -> str:
                if provider == "anthropic":
                    return settings.anthropic_api_key
                if provider == "groq":
                    return settings.groq_api_key
                if provider == "ollama":
                    return None
                return settings.openai_api_key
            
            provider_name = agent_data.get("provider", "openai")
            model = agent_data.get("model", "gpt-4")
            
            # Import and get provider
            from backend.ai.ai_agents.providers import CascadingProvider
            
            cached_provider = CascadingProvider(
                provider=provider_name,
                model=model,
                api_key_fn=_get_api_key,
                premium_provider=agent_data.get("premium_provider"),
                premium_model=agent_data.get("premium_model"),
            )
            
            # Load agent
            agent = await Agent.from_thread(
                thread_id=thread_id,
                conn=db,
                provider=cached_provider,
                temperature=options.get("temperature"),
                memory_strategy=options.get("memory_strategy"),
                memory_n=options.get("memory_n"),
            )
            
            # Generate response ID for streaming
            response_id = str(uuid.uuid4())
            stream_channel = f"stream:{thread_id}:{response_id}"
            
            # Process with or without streaming
            if stream:
                result = await _process_streaming(
                    agent=agent,
                    message=message,
                    user_id=app_ctx.user_id,
                    stream_channel=stream_channel,
                )
            else:
                result = await agent.chat(
                    message,
                    user_id=app_ctx.user_id,
                )
            
            # SCOPE RE-CHECK before confirming result
            thread_check = await db.get_entity("threads", thread_id)
            if not thread_check:
                logger.warning(f"Thread deleted during processing: {thread_id}")
                return {
                    "thread_id": thread_id,
                    "status": "thread_deleted",
                }
            
            metrics.increment("chat_processed")
            if result and result.usage:
                metrics.increment(
                    "tokens",
                    provider=provider_name,
                    model=model,
                )
            
            logger.info(
                f"Chat complete: {thread_id}",
                extra={
                    "thread_id": thread_id,
                    "tokens": result.usage if result else None,
                    "cost": result.cost if result else None,
                }
            )
            
            return {
                "thread_id": thread_id,
                "response_id": response_id,
                "content": result.content if result else "",
                "usage": result.usage if result else None,
                "cost": result.cost if result else None,
                "duration_ms": result.duration_ms if result else None,
                "tool_calls": result.tool_calls if result else None,
                "tool_results": result.tool_results if result else None,
            }
            
        except ScopeError as e:
            logger.error(f"Chat scope check failed: {e}")
            metrics.increment("errors", endpoint="chat", error_type="scope_error")
            raise PermissionError(str(e))
            
        except Exception as e:
            logger.error(
                f"Chat processing failed: {thread_id}",
                extra={"thread_id": thread_id, "error": str(e)},
            )
            metrics.increment("errors", endpoint="chat", error_type=type(e).__name__)
            
            # Publish error to stream if streaming
            if stream:
                await _publish_error(stream_channel, str(e))
            
            raise


async def _process_streaming(
    agent,
    message: str,
    user_id: str,
    stream_channel: str,
):
    """Process chat with streaming, publishing chunks to Redis."""
    redis_client = await _get_redis()
    
    full_content = ""
    result = None
    
    try:
        # Start stream
        if redis_client:
            await redis_client.publish(
                stream_channel,
                json.dumps({"type": "start", "timestamp": datetime.utcnow().isoformat()})
            )
        
        async for chunk in agent.stream(message, user_id=user_id):
            full_content += chunk
            
            # Publish chunk
            if redis_client:
                await redis_client.publish(
                    stream_channel,
                    json.dumps({"type": "chunk", "content": chunk})
                )
        
        # Get final result (agent.stream stores it)
        result = agent._last_result
        
        # Publish completion
        if redis_client:
            await redis_client.publish(
                stream_channel,
                json.dumps({
                    "type": "done",
                    "content": full_content,
                    "usage": result.usage if result else None,
                    "cost": result.cost if result else None,
                })
            )
        
        return result
        
    except Exception as e:
        await _publish_error(stream_channel, str(e))
        raise
