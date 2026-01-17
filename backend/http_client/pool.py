"""
Connection Pool - Reuse HTTP clients and connections.

The Problem:
    Each AsyncHttpClient() creates a new httpx.AsyncClient
    → New TCP connection + TLS handshake (~200-500ms)
    → Even with HTTP/2, cold start is expensive

The Solution:
    Global pool of httpx clients keyed by (base_url, config_hash)
    → First request: Create client (~200ms)
    → Subsequent requests: Reuse connection (~20-50ms)

Usage:
    # Option 1: Use get_client() for automatic pooling
    from http_client import get_client
    
    client = await get_client("https://api.digitalocean.com")
    response = await client.get("/v2/droplets")
    # Don't close! Pool manages lifecycle
    
    # Option 2: Use AsyncHttpClient with pool=True
    from http_client import AsyncHttpClient
    
    async with AsyncHttpClient(base_url="...", pool=True) as client:
        response = await client.get("/users")
    # Connection returned to pool, not closed
    
    # Option 3: Traditional (no pooling - for backwards compatibility)
    async with AsyncHttpClient(base_url="...") as client:
        response = await client.get("/users")
    # Connection closed

Lifecycle:
    - Clients are created on first use
    - Idle clients are closed after max_idle_time (default 5 min)
    - Call close_pool() on app shutdown to clean up
"""

from __future__ import annotations
import asyncio
import time
import hashlib
from typing import Dict, Any, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from .config import HttpConfig


@dataclass
class PooledClientInfo:
    """Metadata for a pooled client."""
    client: Any  # httpx.AsyncClient
    base_url: str
    config_hash: str
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    request_count: int = 0
    
    def touch(self):
        self.last_used = time.time()
        self.request_count += 1
    
    @property
    def idle_seconds(self) -> float:
        return time.time() - self.last_used
    
    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at


class ConnectionPool:
    """
    Global pool of HTTP clients for connection reuse.
    
    Thread-safe for async usage with asyncio.Lock.
    """
    
    def __init__(
        self,
        max_idle_time: float = 300.0,  # 5 minutes
        cleanup_interval: float = 60.0,  # 1 minute
        max_clients: int = 50,  # Max clients in pool
    ):
        self._clients: Dict[str, PooledClientInfo] = {}
        self._lock = asyncio.Lock()
        self._max_idle_time = max_idle_time
        self._cleanup_interval = cleanup_interval
        self._max_clients = max_clients
        self._last_cleanup = 0.0
        
        # Stats
        self._hits = 0
        self._misses = 0
    
    def _make_key(self, base_url: str, config: "HttpConfig" = None) -> str:
        """Create unique key for client lookup."""
        config_str = ""
        if config:
            # Hash relevant config fields
            config_str = f"{config.timeout}:{config.connect_timeout}"
        
        combined = f"{base_url}:{config_str}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]
    
    async def get_or_create(
        self,
        base_url: str,
        config: "HttpConfig" = None,
        http2: bool = True,
    ) -> Any:
        """
        Get existing client or create new one.
        
        Returns the underlying httpx.AsyncClient for direct use.
        """
        key = self._make_key(base_url, config)
        
        async with self._lock:
            # Cleanup if needed
            await self._maybe_cleanup()
            
            # Check for existing client
            if key in self._clients:
                info = self._clients[key]
                if not info.client.is_closed:
                    info.touch()
                    self._hits += 1
                    return info.client
                else:
                    # Client was closed externally, remove it
                    del self._clients[key]
            
            # Create new client
            self._misses += 1
            client = await self._create_client(base_url, config, http2)
            
            # Add to pool (evict oldest if full)
            if len(self._clients) >= self._max_clients:
                await self._evict_oldest()
            
            self._clients[key] = PooledClientInfo(
                client=client,
                base_url=base_url,
                config_hash=key,
            )
            
            return client
    
    async def _create_client(
        self,
        base_url: str,
        config: "HttpConfig" = None,
        http2: bool = True,
    ) -> Any:
        """Create a new httpx.AsyncClient."""
        try:
            import httpx
        except ImportError:
            raise ImportError(
                "httpx is required for connection pooling. "
                "Install it with: pip install httpx[http2]"
            )
        
        # Default config values
        timeout_val = config.timeout if config else 30.0
        connect_timeout = config.connect_timeout if config else 10.0
        
        timeout = httpx.Timeout(
            timeout=timeout_val,
            connect=connect_timeout,
            read=timeout_val,
            write=30.0,
        )
        
        # Generous connection limits for pooling
        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=100,
            keepalive_expiry=60.0,  # Keep connections alive longer
        )
        
        return httpx.AsyncClient(
            base_url=base_url,
            http2=http2,
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
        )
    
    async def _maybe_cleanup(self):
        """Remove idle clients periodically."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        
        self._last_cleanup = now
        
        expired = [
            key for key, info in self._clients.items()
            if info.idle_seconds > self._max_idle_time
        ]
        
        for key in expired:
            info = self._clients.pop(key, None)
            if info:
                try:
                    await info.client.aclose()
                except Exception:
                    pass
    
    async def _evict_oldest(self):
        """Remove the oldest client to make room."""
        if not self._clients:
            return
        
        oldest_key = min(
            self._clients.keys(),
            key=lambda k: self._clients[k].last_used
        )
        
        info = self._clients.pop(oldest_key, None)
        if info:
            try:
                await info.client.aclose()
            except Exception:
                pass
    
    async def close(self):
        """Close all pooled clients."""
        async with self._lock:
            for info in self._clients.values():
                try:
                    await info.client.aclose()
                except Exception:
                    pass
            self._clients.clear()
    
    def stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        total_requests = self._hits + self._misses
        return {
            "active_clients": len(self._clients),
            "total_requests": total_requests,
            "cache_hits": self._hits,
            "cache_misses": self._misses,
            "hit_rate": self._hits / total_requests if total_requests > 0 else 0,
            "clients": [
                {
                    "base_url": info.base_url[:50] + "..." if len(info.base_url) > 50 else info.base_url,
                    "age_seconds": round(info.age_seconds, 1),
                    "idle_seconds": round(info.idle_seconds, 1),
                    "request_count": info.request_count,
                }
                for info in self._clients.values()
            ],
        }


# Global pool instance
_pool: Optional[ConnectionPool] = None
_pool_lock = asyncio.Lock()


async def get_pool() -> ConnectionPool:
    """Get the global connection pool (creates if needed)."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool()
    return _pool


async def get_pooled_client(
    base_url: str,
    config: "HttpConfig" = None,
    http2: bool = True,
) -> Any:
    """
    Get a pooled httpx client for the given base URL.
    
    Connections are reused across requests.
    Do NOT close the returned client - the pool manages lifecycle.
    
    Args:
        base_url: Base URL for the client
        config: Optional HttpConfig for timeout settings
        http2: Enable HTTP/2 (default True)
        
    Returns:
        httpx.AsyncClient ready to use
        
    Example:
        client = await get_pooled_client("https://api.digitalocean.com")
        response = await client.get("/v2/droplets", headers={"Authorization": "Bearer xxx"})
    """
    pool = await get_pool()
    return await pool.get_or_create(base_url, config, http2)


async def close_pool():
    """Close the global connection pool. Call on app shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool_stats() -> Dict[str, Any]:
    """Get connection pool statistics."""
    if _pool:
        return _pool.stats()
    return {"active_clients": 0, "message": "Pool not initialized"}
