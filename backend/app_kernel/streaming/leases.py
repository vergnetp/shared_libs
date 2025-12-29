"""
Streaming leases - Redis-backed lease limiter.

This is a thin wrapper around the job_queue stream_leases module,
exposing it through the kernel interface.

Falls back to in-memory lease limiting if job_queue is not available.

Usage:
    from app_kernel.streaming import StreamLeaseLimiter, StreamLeaseConfig
    
    # Get the kernel's limiter
    limiter = get_lease_limiter()
    
    # Or create your own with custom config
    limiter = StreamLeaseLimiter(redis_config, StreamLeaseConfig(limit=3))
"""
from dataclasses import dataclass, field
from typing import Optional, Dict
from datetime import datetime, timedelta
import asyncio


# Try to import from job_queue, fall back to in-memory implementation
try:
    from ...job_queue.stream_leases import (
        StreamLeaseConfig,
        StreamLeaseLimiter,
    )
    JOB_QUEUE_AVAILABLE = True
except ImportError:
    JOB_QUEUE_AVAILABLE = False
    
    @dataclass
    class StreamLeaseConfig:
        """Configuration for stream lease limiting."""
        limit: int = 3
        ttl_seconds: int = 360
    
    class StreamLeaseLimiter:
        """
        In-memory fallback for stream lease limiting.
        
        For production with multiple workers, use Redis-backed version
        by installing job_queue.
        """
        def __init__(self, redis_config=None, config: StreamLeaseConfig = None):
            self.config = config or StreamLeaseConfig()
            self._leases: Dict[str, list] = {}  # user_id -> [(lease_id, expires_at)]
            self._lock = asyncio.Lock()
        
        async def acquire(self, user_id: str) -> Optional[str]:
            """Acquire a lease for user. Returns lease_id or None if limit reached."""
            async with self._lock:
                now = datetime.utcnow()
                
                # Clean expired leases
                if user_id in self._leases:
                    self._leases[user_id] = [
                        (lid, exp) for lid, exp in self._leases[user_id]
                        if exp > now
                    ]
                else:
                    self._leases[user_id] = []
                
                # Check limit
                if len(self._leases[user_id]) >= self.config.limit:
                    return None
                
                # Create new lease
                import uuid
                lease_id = str(uuid.uuid4())
                expires_at = now + timedelta(seconds=self.config.ttl_seconds)
                self._leases[user_id].append((lease_id, expires_at))
                
                return lease_id
        
        async def release(self, user_id: str, lease_id: str) -> bool:
            """Release a lease."""
            async with self._lock:
                if user_id not in self._leases:
                    return False
                
                original_len = len(self._leases[user_id])
                self._leases[user_id] = [
                    (lid, exp) for lid, exp in self._leases[user_id]
                    if lid != lease_id
                ]
                return len(self._leases[user_id]) < original_len
        
        async def count(self, user_id: str) -> int:
            """Get current lease count for user."""
            async with self._lock:
                now = datetime.utcnow()
                if user_id not in self._leases:
                    return 0
                return len([1 for _, exp in self._leases[user_id] if exp > now])


# Module-level limiter, initialized by init_app_kernel()
_lease_limiter: Optional[StreamLeaseLimiter] = None


def init_lease_limiter(redis_config=None, config: Optional[StreamLeaseConfig] = None):
    """Initialize the lease limiter. Called by init_app_kernel()."""
    global _lease_limiter
    _lease_limiter = StreamLeaseLimiter(redis_config, config)


def get_lease_limiter() -> StreamLeaseLimiter:
    """Get the initialized lease limiter."""
    if _lease_limiter is None:
        raise RuntimeError("Lease limiter not initialized. Call init_app_kernel() first.")
    return _lease_limiter
