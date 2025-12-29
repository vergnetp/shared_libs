"""
Streaming lifecycle - Async context manager for safe streaming.

Provides a context manager that:
- Acquires lease on entry
- Always releases on exit (even on error)
- Handles refresh for long-running streams

Apps should NEVER manually touch Redis for streams.

Usage:
    from app_kernel.streaming import stream_lease, StreamLimitExceeded
    
    @app.post("/chat/stream")
    async def stream_chat(user: UserIdentity = Depends(get_current_user)):
        try:
            async with stream_lease(user.id) as lease:
                async for chunk in generate_response():
                    yield chunk
                    
                    # Optional: refresh lease for long streams
                    if should_refresh:
                        lease.refresh()
                        
        except StreamLimitExceeded:
            raise HTTPException(429, "Too many concurrent streams")
"""
from contextlib import asynccontextmanager
from typing import Optional, AsyncIterator
import asyncio

from .leases import get_lease_limiter, StreamLeaseLimiter


class StreamLimitExceeded(Exception):
    """Raised when user has too many concurrent streams."""
    pass


class StreamLease:
    """
    Handle for an active stream lease.
    
    Provides methods to refresh or release the lease.
    """
    
    def __init__(
        self,
        limiter: StreamLeaseLimiter,
        user_id: str,
        lease_id: str
    ):
        self._limiter = limiter
        self._user_id = user_id
        self._lease_id = lease_id
        self._released = False
    
    @property
    def lease_id(self) -> str:
        return self._lease_id
    
    @property
    def user_id(self) -> str:
        return self._user_id
    
    @property
    def is_active(self) -> bool:
        return not self._released
    
    def refresh(self) -> bool:
        """
        Refresh the lease for long-running streams.
        
        Returns True if refresh succeeded, False if lease expired.
        """
        if self._released:
            return False
        return self._limiter.refresh_stream_lease(self._user_id, self._lease_id)
    
    async def refresh_async(self) -> bool:
        """Async version of refresh."""
        return await asyncio.to_thread(self.refresh)
    
    def release(self):
        """Explicitly release the lease."""
        if not self._released:
            self._limiter.release_stream_lease(self._user_id, self._lease_id)
            self._released = True
    
    async def release_async(self):
        """Async version of release."""
        await asyncio.to_thread(self.release)


@asynccontextmanager
async def stream_lease(
    user_id: str,
    limiter: Optional[StreamLeaseLimiter] = None
) -> AsyncIterator[StreamLease]:
    """
    Async context manager for stream lifecycle.
    
    Acquires a lease on entry, releases on exit (always).
    
    Args:
        user_id: User ID to acquire lease for
        limiter: Optional custom limiter (uses kernel's limiter if not provided)
    
    Yields:
        StreamLease handle
    
    Raises:
        StreamLimitExceeded: If user has too many concurrent streams
    
    Usage:
        async with stream_lease(user.id) as lease:
            async for chunk in generate():
                yield chunk
    """
    if limiter is None:
        limiter = get_lease_limiter()
    
    # Acquire lease (sync operation, run in thread)
    lease_id = await asyncio.to_thread(
        limiter.acquire_stream_lease,
        user_id
    )
    
    if lease_id is None:
        raise StreamLimitExceeded(f"User {user_id} has too many concurrent streams")
    
    lease = StreamLease(limiter, user_id, lease_id)
    
    try:
        yield lease
    finally:
        # Always release, even on error
        await lease.release_async()


async def get_active_streams(
    user_id: str,
    limiter: Optional[StreamLeaseLimiter] = None
) -> int:
    """
    Get the number of active streams for a user.
    
    Args:
        user_id: User ID to check
        limiter: Optional custom limiter
    
    Returns:
        Number of active (non-expired) streams
    """
    if limiter is None:
        limiter = get_lease_limiter()
    
    return await asyncio.to_thread(limiter.get_active_streams, user_id)


async def can_start_stream(
    user_id: str,
    limiter: Optional[StreamLeaseLimiter] = None
) -> bool:
    """
    Check if user can start a new stream.
    
    Args:
        user_id: User ID to check
        limiter: Optional custom limiter
    
    Returns:
        True if user can start a new stream
    """
    if limiter is None:
        limiter = get_lease_limiter()
    
    active = await get_active_streams(user_id, limiter)
    return active < limiter.cfg.limit
