"""
Stream Leases - Rate limiting for concurrent streams.

Uses Redis ZSET for distributed, crash-safe lease tracking.
Falls back to in-memory implementation if Redis is unavailable.

Usage:
    # With Redis (production)
    limiter = StreamLeaseLimiter(redis_config, StreamLeaseConfig(limit=5))
    
    # In-memory fallback (dev/testing)
    limiter = InMemoryLeaseLimiter(StreamLeaseConfig(limit=5))
    
    # Acquire/release
    lease_id = limiter.acquire_stream_lease(user_id)
    if lease_id:
        try:
            # ... do streaming ...
        finally:
            limiter.release_stream_lease(user_id, lease_id)
"""

from __future__ import annotations
import uuid
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis


@dataclass
class StreamLeaseConfig:
    """
    Configuration for stream lease limiting.
    
    Attributes:
        limit: Maximum concurrent streams per user (0 = disabled)
        ttl_seconds: Lease TTL - auto-expires if not released (crash recovery)
        key_namespace: Redis key namespace
        key_ttl_grace: Extra seconds to keep Redis key after all leases expire
    """
    limit: int = 5
    ttl_seconds: int = 180  # 3 minutes - covers most streams
    key_namespace: str = "stream_leases"
    key_ttl_grace: int = 60


class StreamLeaseLimiter:
    """
    Redis-backed concurrent stream limiter using ZSET.
    
    Key: {key_prefix}{key_namespace}:{user_id}
    Member: lease_id (uuid hex)
    Score: expires_at (epoch seconds from Redis server time)
    
    Thread-safe. For async FastAPI, call via asyncio.to_thread().
    
    Args:
        redis_config: Redis configuration with get_client() method
        config: StreamLeaseConfig instance
    """
    
    def __init__(self, redis_config, config: Optional[StreamLeaseConfig] = None):
        """
        Initialize the lease limiter.
        
        Args:
            redis_config: Object with get_client() method returning Redis client
            config: Lease configuration (uses defaults if not provided)
        """
        self.redis_config = redis_config
        self.cfg = config or StreamLeaseConfig()
    
    def _key(self, user_id: str) -> str:
        """Get Redis key for user's leases."""
        prefix = getattr(self.redis_config, "key_prefix", "") or ""
        if prefix and not prefix.endswith(":"):
            prefix += ":"
        return f"{prefix}{self.cfg.key_namespace}:{user_id}"
    
    @staticmethod
    def _redis_now(r: 'Redis') -> float:
        """Get Redis server time (avoids clock skew across app servers)."""
        sec, usec = r.time()
        return float(sec) + (float(usec) / 1_000_000.0)
    
    def acquire_stream_lease(self, user_id: str) -> Optional[str]:
        """
        Try to acquire a lease for user.
        
        Args:
            user_id: User identifier
            
        Returns:
            lease_id if acquired, None if limit reached
        """
        if self.cfg.limit <= 0:
            return None
        
        r = self.redis_config.get_client()
        key = self._key(user_id)
        ttl = int(self.cfg.ttl_seconds)
        grace = int(self.cfg.key_ttl_grace)
        
        lease_id = uuid.uuid4().hex
        
        # Import here to avoid circular dependency
        from redis import WatchError
        
        pipe = r.pipeline(transaction=True)
        try:
            for _ in range(10):  # Retry on WATCH contention
                now = self._redis_now(r)
                expires_at = now + ttl
                
                try:
                    pipe.watch(key)
                    
                    # Remove expired leases
                    pipe.zremrangebyscore(key, 0, now)
                    
                    # Count active leases
                    active_raw = pipe.zcard(key)
                    active = int(active_raw)
                    
                    if active >= int(self.cfg.limit):
                        pipe.unwatch()
                        return None
                    
                    # Atomically add new lease and set key TTL
                    pipe.multi()
                    pipe.zadd(key, {lease_id: expires_at})
                    pipe.expire(key, ttl + grace)
                    pipe.execute()
                    return lease_id
                
                except WatchError:
                    # Key changed between WATCH and EXEC; retry
                    continue
        finally:
            pipe.reset()
        
        return None
    
    def release_stream_lease(self, user_id: str, lease_id: str) -> None:
        """
        Release a previously acquired lease.
        
        Safe to call even if lease already expired/released.
        
        Args:
            user_id: User identifier
            lease_id: Lease ID from acquire_stream_lease()
        """
        r = self.redis_config.get_client()
        key = self._key(user_id)
        
        pipe = r.pipeline(transaction=True)
        try:
            pipe.zrem(key, lease_id)
            pipe.zcard(key)
            _, remaining_raw = pipe.execute()
            remaining = int(remaining_raw)
        finally:
            pipe.reset()
        
        # Clean up empty key
        if remaining <= 0:
            r.delete(key)
    
    def refresh_stream_lease(self, user_id: str, lease_id: str) -> bool:
        """
        Extend lease TTL for long-running streams.
        
        Args:
            user_id: User identifier
            lease_id: Lease ID to refresh
            
        Returns:
            True if refreshed, False if lease doesn't exist (expired/released)
        """
        r = self.redis_config.get_client()
        key = self._key(user_id)
        ttl = int(self.cfg.ttl_seconds)
        grace = int(self.cfg.key_ttl_grace)
        
        now = self._redis_now(r)
        expires_at = now + ttl
        
        # Only refresh if present
        if r.zscore(key, lease_id) is None:
            return False
        
        r.zadd(key, {lease_id: expires_at})
        r.expire(key, ttl + grace)
        return True
    
    def get_active_streams(self, user_id: str) -> int:
        """
        Get count of active (non-expired) leases for user.
        
        Args:
            user_id: User identifier
            
        Returns:
            Number of active streams
        """
        r = self.redis_config.get_client()
        key = self._key(user_id)
        now = self._redis_now(r)
        
        pipe = r.pipeline(transaction=True)
        try:
            pipe.zremrangebyscore(key, 0, now)
            pipe.zcard(key)
            _, active_raw = pipe.execute()
            return int(active_raw)
        finally:
            pipe.reset()
    
    def get_all_leases(self, user_id: str) -> List[Tuple[str, float]]:
        """
        Get all active leases for user (for debugging).
        
        Args:
            user_id: User identifier
            
        Returns:
            List of (lease_id, expires_at) tuples
        """
        r = self.redis_config.get_client()
        key = self._key(user_id)
        now = self._redis_now(r)
        
        # Remove expired first
        r.zremrangebyscore(key, 0, now)
        
        # Get remaining
        leases = r.zrange(key, 0, -1, withscores=True)
        return [(lid, score) for lid, score in leases]


class InMemoryLeaseLimiter:
    """
    In-memory fallback for stream lease limiting.
    
    For development/testing only. NOT suitable for production
    with multiple workers (no shared state).
    
    Thread-safe within a single process.
    """
    
    def __init__(self, config: Optional[StreamLeaseConfig] = None):
        self.cfg = config or StreamLeaseConfig()
        self._leases: Dict[str, List[Tuple[str, datetime]]] = {}  # user_id -> [(lease_id, expires_at)]
        self._lock = threading.RLock()
    
    def acquire_stream_lease(self, user_id: str) -> Optional[str]:
        """Acquire a lease for user. Returns lease_id or None."""
        if self.cfg.limit <= 0:
            return None
        
        with self._lock:
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
            if len(self._leases[user_id]) >= self.cfg.limit:
                return None
            
            # Create new lease
            lease_id = uuid.uuid4().hex
            expires_at = now + timedelta(seconds=self.cfg.ttl_seconds)
            self._leases[user_id].append((lease_id, expires_at))
            
            return lease_id
    
    def release_stream_lease(self, user_id: str, lease_id: str) -> None:
        """Release a lease."""
        with self._lock:
            if user_id not in self._leases:
                return
            
            self._leases[user_id] = [
                (lid, exp) for lid, exp in self._leases[user_id]
                if lid != lease_id
            ]
            
            # Clean up empty entry
            if not self._leases[user_id]:
                del self._leases[user_id]
    
    def refresh_stream_lease(self, user_id: str, lease_id: str) -> bool:
        """Refresh a lease. Returns False if not found."""
        with self._lock:
            if user_id not in self._leases:
                return False
            
            now = datetime.utcnow()
            new_expires = now + timedelta(seconds=self.cfg.ttl_seconds)
            
            for i, (lid, exp) in enumerate(self._leases[user_id]):
                if lid == lease_id and exp > now:
                    self._leases[user_id][i] = (lid, new_expires)
                    return True
            
            return False
    
    def get_active_streams(self, user_id: str) -> int:
        """Get count of active streams for user."""
        with self._lock:
            now = datetime.utcnow()
            if user_id not in self._leases:
                return 0
            return len([1 for _, exp in self._leases[user_id] if exp > now])
    
    def get_all_leases(self, user_id: str) -> List[Tuple[str, float]]:
        """Get all active leases for user."""
        with self._lock:
            now = datetime.utcnow()
            if user_id not in self._leases:
                return []
            return [
                (lid, exp.timestamp())
                for lid, exp in self._leases[user_id]
                if exp > now
            ]


# Module-level limiter (initialized by init_streaming())
_lease_limiter: Optional[StreamLeaseLimiter] = None


def init_lease_limiter(
    redis_config=None,
    config: Optional[StreamLeaseConfig] = None,
    use_memory: bool = False,
) -> StreamLeaseLimiter:
    """
    Initialize the global lease limiter.
    
    Args:
        redis_config: Redis configuration (required unless use_memory=True)
        config: Lease configuration
        use_memory: Use in-memory implementation (dev/testing only)
    
    Returns:
        Initialized limiter
    """
    global _lease_limiter
    
    if use_memory:
        _lease_limiter = InMemoryLeaseLimiter(config)
    else:
        if redis_config is None:
            raise ValueError("redis_config required (or set use_memory=True)")
        _lease_limiter = StreamLeaseLimiter(redis_config, config)
    
    return _lease_limiter


def get_lease_limiter() -> StreamLeaseLimiter:
    """Get the initialized lease limiter."""
    if _lease_limiter is None:
        raise RuntimeError("Lease limiter not initialized. Call init_lease_limiter() first.")
    return _lease_limiter
