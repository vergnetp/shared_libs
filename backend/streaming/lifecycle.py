"""
Stream Lifecycle - Async context manager for safe streaming.

Provides a context manager that:
- Acquires lease on entry
- Always releases on exit (even on error)
- Handles refresh for long-running streams

Apps should use this instead of manually managing leases.

Usage:
    from streaming import stream_lease, StreamLimitExceeded
    
    @app.post("/chat/stream")
    async def stream_chat(user: UserIdentity = Depends(get_current_user)):
        try:
            async with stream_lease(user.id) as lease:
                async for chunk in generate_response():
                    yield chunk
                    
                    # Optional: refresh lease for long streams
                    if should_refresh:
                        await lease.refresh_async()
                        
        except StreamLimitExceeded:
            raise HTTPException(429, "Too many concurrent streams")
"""

from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Optional, AsyncIterator, Union, TYPE_CHECKING
import asyncio

from .leases import (
    get_lease_limiter,
    StreamLeaseLimiter,
    InMemoryLeaseLimiter,
    StreamLeaseConfig,
)

if TYPE_CHECKING:
    LimiterType = Union[StreamLeaseLimiter, InMemoryLeaseLimiter]


class StreamLimitExceeded(Exception):
    """
    Raised when user has too many concurrent streams.
    
    Attributes:
        user_id: The user who hit the limit
        limit: The configured limit
        active: Current number of active streams
    """
    
    def __init__(
        self,
        user_id: str,
        limit: Optional[int] = None,
        active: Optional[int] = None,
    ):
        self.user_id = user_id
        self.limit = limit
        self.active = active
        
        msg = f"User {user_id} has too many concurrent streams"
        if limit is not None:
            msg += f" (limit: {limit})"
        if active is not None:
            msg += f" (active: {active})"
        
        super().__init__(msg)


class StreamLease:
    """
    Handle for an active stream lease.
    
    Provides methods to refresh or release the lease.
    Tracks lease state to prevent double-release.
    
    Attributes:
        lease_id: The unique lease identifier
        user_id: The user who owns the lease
        is_active: Whether the lease is still active
    """
    
    def __init__(
        self,
        limiter: 'LimiterType',
        user_id: str,
        lease_id: str,
    ):
        self._limiter = limiter
        self._user_id = user_id
        self._lease_id = lease_id
        self._released = False
    
    @property
    def lease_id(self) -> str:
        """The unique lease identifier."""
        return self._lease_id
    
    @property
    def user_id(self) -> str:
        """The user who owns the lease."""
        return self._user_id
    
    @property
    def is_active(self) -> bool:
        """Whether the lease is still active (not released)."""
        return not self._released
    
    def refresh(self) -> bool:
        """
        Refresh the lease TTL for long-running streams.
        
        Call this periodically for streams that may exceed the TTL.
        
        Returns:
            True if refresh succeeded, False if lease expired/released
        """
        if self._released:
            return False
        return self._limiter.refresh_stream_lease(self._user_id, self._lease_id)
    
    async def refresh_async(self) -> bool:
        """
        Async version of refresh().
        
        Runs the sync operation in a thread pool.
        """
        return await asyncio.to_thread(self.refresh)
    
    def release(self) -> None:
        """
        Explicitly release the lease.
        
        Safe to call multiple times (idempotent).
        """
        if not self._released:
            self._limiter.release_stream_lease(self._user_id, self._lease_id)
            self._released = True
    
    async def release_async(self) -> None:
        """
        Async version of release().
        
        Runs the sync operation in a thread pool.
        """
        await asyncio.to_thread(self.release)
    
    def __repr__(self) -> str:
        status = "active" if self.is_active else "released"
        return f"StreamLease({self._user_id}, {self._lease_id[:8]}..., {status})"


@asynccontextmanager
async def stream_lease(
    user_id: str,
    limiter: Optional['LimiterType'] = None,
) -> AsyncIterator[StreamLease]:
    """
    Async context manager for stream lifecycle.
    
    Acquires a lease on entry, releases on exit (always, even on error).
    
    Args:
        user_id: User ID to acquire lease for
        limiter: Optional custom limiter (uses global limiter if not provided)
    
    Yields:
        StreamLease handle for the acquired lease
    
    Raises:
        StreamLimitExceeded: If user has too many concurrent streams
    
    Example:
        async with stream_lease(user.id) as lease:
            async for chunk in generate():
                yield chunk
                
                # Refresh for long streams
                if chunk_count % 100 == 0:
                    await lease.refresh_async()
    """
    if limiter is None:
        limiter = get_lease_limiter()
    
    # Acquire lease (sync operation, run in thread)
    lease_id = await asyncio.to_thread(
        limiter.acquire_stream_lease,
        user_id
    )
    
    if lease_id is None:
        # Get details for error message
        active = await asyncio.to_thread(limiter.get_active_streams, user_id)
        limit = limiter.cfg.limit
        raise StreamLimitExceeded(user_id, limit=limit, active=active)
    
    lease = StreamLease(limiter, user_id, lease_id)
    
    try:
        yield lease
    finally:
        # Always release, even on error
        await lease.release_async()


async def get_active_streams(
    user_id: str,
    limiter: Optional['LimiterType'] = None,
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
    limiter: Optional['LimiterType'] = None,
) -> bool:
    """
    Check if user can start a new stream without blocking.
    
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


def stream_lease_sync(
    user_id: str,
    limiter: Optional['LimiterType'] = None,
):
    """
    Sync context manager for stream lifecycle.
    
    For use in synchronous code (CLI, background workers).
    
    Args:
        user_id: User ID to acquire lease for
        limiter: Optional custom limiter
    
    Yields:
        StreamLease handle
    
    Example:
        with stream_lease_sync(user_id) as lease:
            for chunk in generate():
                yield chunk
    """
    from contextlib import contextmanager
    
    @contextmanager
    def _sync_lease():
        if limiter is None:
            _limiter = get_lease_limiter()
        else:
            _limiter = limiter
        
        lease_id = _limiter.acquire_stream_lease(user_id)
        
        if lease_id is None:
            active = _limiter.get_active_streams(user_id)
            raise StreamLimitExceeded(user_id, limit=_limiter.cfg.limit, active=active)
        
        lease = StreamLease(_limiter, user_id, lease_id)
        
        try:
            yield lease
        finally:
            lease.release()
    
    return _sync_lease()
