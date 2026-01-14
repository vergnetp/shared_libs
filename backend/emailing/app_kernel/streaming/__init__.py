"""
app_kernel.streaming - Streaming lifecycle safety.

This module provides:
- Redis-backed lease limiter
- Async context manager for safe streaming
- Automatic cleanup on exit

Apps should NEVER manually touch Redis for streams.

Usage:
    from app_kernel.streaming import stream_lease, StreamLimitExceeded
    
    @app.post("/chat/stream")
    async def stream_chat(user: UserIdentity = Depends(get_current_user)):
        try:
            async with stream_lease(user.id) as lease:
                async for chunk in generate():
                    yield chunk
        except StreamLimitExceeded:
            raise HTTPException(429, "Too many concurrent streams")
"""

from .leases import (
    StreamLeaseConfig,
    StreamLeaseLimiter,
    init_lease_limiter,
    get_lease_limiter,
)

from .lifecycle import (
    StreamLimitExceeded,
    StreamLease,
    stream_lease,
    get_active_streams,
    can_start_stream,
)

__all__ = [
    # Leases
    "StreamLeaseConfig",
    "StreamLeaseLimiter",
    "init_lease_limiter",
    "get_lease_limiter",
    
    # Lifecycle
    "StreamLimitExceeded",
    "StreamLease",
    "stream_lease",
    "get_active_streams",
    "can_start_stream",
]
