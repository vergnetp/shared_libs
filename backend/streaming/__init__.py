"""
Streaming Module - Real-time event streaming infrastructure.

This module provides two streaming patterns:

1. **Direct Streaming** (LLM, fast tokens)
   - Uses lease limiter to cap concurrent streams
   - No Redis hop between tokens (lowest latency)
   - Use: `stream_lease()` context manager + native StreamingResponse

2. **Queue-based Streaming** (deployments, long tasks)  
   - Background worker publishes to Redis Pub/Sub
   - FastAPI subscribes and yields SSE events
   - Non-blocking (doesn't hold up FastAPI workers)
   - Use: `StreamContext` + `sse_response()`

Quick Start:
    
    # Initialize (in app startup)
    from streaming import init_streaming
    init_streaming(redis_config)
    
    # LLM streaming (direct, lease-limited)
    from streaming import stream_lease, StreamLimitExceeded
    
    async with stream_lease(user.id) as lease:
        async for token in llm.stream(prompt):
            yield token
    
    # Deployment streaming (queue-based)
    from streaming import StreamContext, sse_response
    
    ctx = StreamContext.create(
        workspace_id=user.id,
        project="myapp",
        persist_events=True,
    )
    queue_manager.enqueue(entity={"ctx": ctx.to_dict()}, processor=task)
    return await sse_response(ctx.channel_id)
    
    # In background worker
    def task(entity):
        ctx = StreamContext.from_dict(entity["ctx"])
        ctx.log("Starting...")
        ctx.progress(50)
        ctx.complete(success=True)
"""

from typing import Optional, TYPE_CHECKING

# Events
from .events import (
    StreamEvent,
    EventType,
)

# Lease Limiting
from .leases import (
    StreamLeaseConfig,
    StreamLeaseLimiter,
    InMemoryLeaseLimiter,
    init_lease_limiter,
    get_lease_limiter,
)

# Lifecycle (context managers)
from .lifecycle import (
    StreamLease,
    StreamLimitExceeded,
    stream_lease,
    stream_lease_sync,
    get_active_streams,
    can_start_stream,
)

# Channels (Redis Pub/Sub)
from .channels import (
    ChannelConfig,
    SyncStreamChannel,
    AsyncStreamChannel,
    get_sync_channel,
    get_async_channel,
    init_channels,
)

# Context (for background workers)
from .context import (
    StreamContext,
    Context,  # Alias
)

# SSE Helpers (for FastAPI)
from .sse import (
    sse_generator,
    sse_response,
    sse_response_with_lease,
    direct_sse_response,
    SSEEmitter,  # Legacy compatibility
)

# Storage (optional OpenSearch persistence)
from .storage import (
    EventStorageConfig,
    EventStorageInterface,
    OpenSearchEventStorage,
    InMemoryEventStorage,
    init_event_storage,
    get_event_storage,
    is_storage_initialized,
)


def init_streaming(
    redis_config,
    lease_config: Optional[StreamLeaseConfig] = None,
    channel_config: Optional[ChannelConfig] = None,
    storage_config: Optional[EventStorageConfig] = None,
    enable_storage: bool = False,
):
    """
    Initialize all streaming components.
    
    Call this during application startup.
    
    Args:
        redis_config: Redis configuration with get_client() method
        lease_config: Optional lease limiter configuration
        channel_config: Optional channel configuration  
        storage_config: Optional event storage configuration
        enable_storage: Whether to enable OpenSearch event storage
        
    Example:
        from shared_libs.backend.job_queue import QueueRedisConfig
        from streaming import init_streaming
        
        redis_config = QueueRedisConfig(url="redis://localhost:6379/0")
        init_streaming(redis_config, enable_storage=True)
    """
    # Initialize lease limiter
    init_lease_limiter(redis_config, lease_config)
    
    # Initialize channels
    init_channels(redis_config, channel_config)
    
    # Initialize storage (optional)
    if enable_storage:
        init_event_storage(storage_config)


__all__ = [
    # Events
    "StreamEvent",
    "EventType",
    
    # Lease Limiting
    "StreamLeaseConfig",
    "StreamLeaseLimiter", 
    "InMemoryLeaseLimiter",
    "init_lease_limiter",
    "get_lease_limiter",
    
    # Lifecycle
    "StreamLease",
    "StreamLimitExceeded",
    "stream_lease",
    "stream_lease_sync",
    "get_active_streams",
    "can_start_stream",
    
    # Channels
    "ChannelConfig",
    "SyncStreamChannel",
    "AsyncStreamChannel",
    "get_sync_channel",
    "get_async_channel",
    "init_channels",
    
    # Context
    "StreamContext",
    "Context",
    
    # SSE Helpers
    "sse_generator",
    "sse_response",
    "sse_response_with_lease",
    "direct_sse_response",
    "SSEEmitter",
    
    # Storage
    "EventStorageConfig",
    "EventStorageInterface",
    "OpenSearchEventStorage",
    "InMemoryEventStorage",
    "init_event_storage",
    "get_event_storage",
    "is_storage_initialized",
    
    # Initialization
    "init_streaming",
]
