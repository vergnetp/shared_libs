"""
Connection Pool - Reuse HTTP clients and connections.

The Problem:
    Each AsyncHttpClient() creates a new httpx.AsyncClient
    → New TCP connection + TLS handshake (~200-500ms)
    → Even with HTTP/2, cold start is expensive

The Solution:
    Global pool of clients keyed by (base_url, config_hash)
    → First request: Create client (~200ms)
    → Subsequent requests: Reuse connection (~20-50ms)

Usage:
    # Async - returns AsyncHttpClient with full features (retry, CB, tracing)
    from http_client import get_pooled_client
    
    client = await get_pooled_client("https://api.digitalocean.com")
    client.set_bearer_token("xxx")
    response = await client.get("/v2/droplets")
    # Don't close! Pool manages lifecycle
    
    # Sync - returns SyncHttpClient  
    from http_client import get_pooled_sync_client
    
    client = get_pooled_sync_client("https://api.stripe.com/v1")
    response = client.post("/products", data=form_data)

Lifecycle:
    - Clients are created on first use
    - Idle clients are closed after max_idle_time (default 5 min)
    - Call close_pool() on app shutdown to clean up
"""

from __future__ import annotations
import asyncio
import time
import hashlib
import threading
from typing import Dict, Any, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from .config import HttpConfig
    from .clients.async_client import AsyncHttpClient
    from .clients.sync_client import SyncHttpClient


@dataclass
class PooledClientInfo:
    """Metadata for a pooled client."""
    client: Any  # httpx.AsyncClient or requests.Session
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


@dataclass
class PoolLimits:
    """
    Connection pool limits per base_url.
    
    These control httpx's internal connection pooling for each base URL.
    With 1000 users hitting the same API, requests queue for available connections.
    """
    max_connections: int = 100          # Max concurrent connections per base_url
    max_keepalive: int = 20             # Idle connections to keep warm
    keepalive_expiry: float = 60.0      # Seconds before closing idle connection
    
    # Presets for common scenarios
    @classmethod
    def default(cls) -> "PoolLimits":
        """Standard settings - good for most APIs."""
        return cls(max_connections=100, max_keepalive=20)
    
    @classmethod  
    def high_concurrency(cls) -> "PoolLimits":
        """For high-traffic scenarios (LLM streaming, many users)."""
        return cls(max_connections=200, max_keepalive=50, keepalive_expiry=120.0)
    
    @classmethod
    def low_traffic(cls) -> "PoolLimits":
        """For scripts or low-traffic apps."""
        return cls(max_connections=20, max_keepalive=5, keepalive_expiry=30.0)


# Global pool limits (can be changed before first use)
_pool_limits = PoolLimits.default()


def configure_pool_limits(limits: PoolLimits) -> None:
    """
    Configure pool limits. Call before first request.
    
    Example:
        from http_client import configure_pool_limits, PoolLimits
        configure_pool_limits(PoolLimits.high_concurrency())
    """
    global _pool_limits
    _pool_limits = limits


# =============================================================================
# Async Connection Pool
# =============================================================================

class AsyncConnectionPool:
    """
    Global pool of async HTTP clients for connection reuse.
    
    Thread-safe for async usage with asyncio.Lock.
    Returns AsyncHttpClient instances with full features (retry, CB, tracing).
    """
    
    def __init__(
        self,
        max_idle_time: float = 300.0,  # 5 minutes
        cleanup_interval: float = 60.0,  # 1 minute
        max_clients: int = 50,  # Max clients in pool
    ):
        self._httpx_clients: Dict[str, PooledClientInfo] = {}
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
    
    async def get_client(
        self,
        base_url: str,
        config: "HttpConfig" = None,
        http2: bool = False,
    ) -> "AsyncHttpClient":
        """
        Get an AsyncHttpClient with connection reuse.
        
        The returned client has full features:
        - Automatic retry with exponential backoff
        - Circuit breaker (per base_url)
        - Tracing spans
        - Connection reuse via shared httpx client
        
        Do NOT close the returned client - the pool manages lifecycle.
        """
        key = self._make_key(base_url, config)
        
        async with self._lock:
            # Cleanup if needed
            await self._maybe_cleanup()
            
            # Check for existing httpx client
            if key in self._httpx_clients:
                info = self._httpx_clients[key]
                if not info.client.is_closed:
                    info.touch()
                    self._hits += 1
                    # Wrap existing httpx client in AsyncHttpClient
                    return self._wrap_client(info.client, base_url, config)
                else:
                    # Client was closed externally, remove it
                    del self._httpx_clients[key]
            
            # Create new httpx client
            self._misses += 1
            httpx_client = await self._create_httpx_client(base_url, config, http2)
            
            # Add to pool (evict oldest if full)
            if len(self._httpx_clients) >= self._max_clients:
                await self._evict_oldest()
            
            self._httpx_clients[key] = PooledClientInfo(
                client=httpx_client,
                base_url=base_url,
                config_hash=key,
            )
            
            return self._wrap_client(httpx_client, base_url, config)
    
    def _wrap_client(
        self,
        httpx_client,
        base_url: str,
        config: "HttpConfig" = None,
    ) -> "AsyncHttpClient":
        """Wrap httpx client in AsyncHttpClient with full features."""
        from .clients.async_client import AsyncHttpClient
        from .config import HttpConfig
        
        client = AsyncHttpClient(
            config=config or HttpConfig(),
            base_url=base_url,
            circuit_breaker_name=base_url,  # CB per base_url
        )
        # Inject the pooled httpx client
        client._inject_client(httpx_client)
        return client
    
    async def _create_httpx_client(
        self,
        base_url: str,
        config: "HttpConfig" = None,
        http2: bool = False,
    ):
        """Create a new httpx.AsyncClient."""
        try:
            import httpx
        except ImportError:
            raise ImportError(
                "httpx is required for connection pooling. "
                "Install it with: pip install httpx"
            )
        
        # Default config values
        from .config import HttpConfig
        config = config or HttpConfig()
        
        timeout = httpx.Timeout(
            timeout=config.timeout,
            connect=config.connect_timeout,
            read=config.get_read_timeout(),
            write=30.0,
        )
        
        # Use global pool limits
        limits = httpx.Limits(
            max_keepalive_connections=_pool_limits.max_keepalive,
            max_connections=_pool_limits.max_connections,
            keepalive_expiry=_pool_limits.keepalive_expiry,
        )
        
        return httpx.AsyncClient(
            base_url=base_url,
            http2=http2,
            timeout=timeout,
            limits=limits,
            follow_redirects=config.follow_redirects,
        )
    
    async def _maybe_cleanup(self):
        """Remove idle clients periodically."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        
        self._last_cleanup = now
        
        expired = [
            key for key, info in self._httpx_clients.items()
            if info.idle_seconds > self._max_idle_time
        ]
        
        for key in expired:
            info = self._httpx_clients.pop(key, None)
            if info:
                try:
                    await info.client.aclose()
                except Exception:
                    pass
    
    async def _evict_oldest(self):
        """Remove the oldest client to make room."""
        if not self._httpx_clients:
            return
        
        oldest_key = min(
            self._httpx_clients.keys(),
            key=lambda k: self._httpx_clients[k].last_used
        )
        
        info = self._httpx_clients.pop(oldest_key, None)
        if info:
            try:
                await info.client.aclose()
            except Exception:
                pass
    
    async def close(self):
        """Close all pooled clients."""
        async with self._lock:
            for info in self._httpx_clients.values():
                try:
                    await info.client.aclose()
                except Exception:
                    pass
            self._httpx_clients.clear()
    
    def stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        total_requests = self._hits + self._misses
        return {
            "active_clients": len(self._httpx_clients),
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
                for info in self._httpx_clients.values()
            ],
        }


# =============================================================================
# Sync Connection Pool
# =============================================================================

class SyncConnectionPool:
    """
    Global pool of sync HTTP clients for connection reuse.
    
    Thread-safe with threading.Lock.
    Returns SyncHttpClient instances with full features (retry, CB, tracing).
    """
    
    def __init__(
        self,
        max_idle_time: float = 300.0,  # 5 minutes
        cleanup_interval: float = 60.0,  # 1 minute
        max_clients: int = 50,  # Max clients in pool
    ):
        self._sessions: Dict[str, PooledClientInfo] = {}
        self._lock = threading.Lock()
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
            config_str = f"{config.timeout}:{config.connect_timeout}"
        
        combined = f"{base_url}:{config_str}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]
    
    def get_client(
        self,
        base_url: str,
        config: "HttpConfig" = None,
    ) -> "SyncHttpClient":
        """
        Get a SyncHttpClient with connection reuse.
        
        The returned client has full features:
        - Automatic retry with exponential backoff
        - Circuit breaker (per base_url)
        - Tracing spans
        - Connection reuse via shared requests.Session
        
        Do NOT close the returned client - the pool manages lifecycle.
        """
        key = self._make_key(base_url, config)
        
        with self._lock:
            # Cleanup if needed
            self._maybe_cleanup()
            
            # Check for existing session
            if key in self._sessions:
                info = self._sessions[key]
                info.touch()
                self._hits += 1
                return self._wrap_session(info.client, base_url, config)
            
            # Create new session
            self._misses += 1
            session = self._create_session(config)
            
            # Add to pool (evict oldest if full)
            if len(self._sessions) >= self._max_clients:
                self._evict_oldest()
            
            self._sessions[key] = PooledClientInfo(
                client=session,
                base_url=base_url,
                config_hash=key,
            )
            
            return self._wrap_session(session, base_url, config)
    
    def _wrap_session(
        self,
        session,
        base_url: str,
        config: "HttpConfig" = None,
    ) -> "SyncHttpClient":
        """Wrap session in SyncHttpClient with full features."""
        from .clients.sync_client import SyncHttpClient
        from .config import HttpConfig
        
        client = SyncHttpClient(
            config=config or HttpConfig(),
            base_url=base_url,
            circuit_breaker_name=base_url,  # CB per base_url
        )
        # Inject the pooled session
        client._inject_session(session)
        return client
    
    def _create_session(self, config: "HttpConfig" = None):
        """Create a new requests.Session."""
        import requests
        from .config import HttpConfig
        
        config = config or HttpConfig()
        session = requests.Session()
        session.headers.update(config.get_default_headers())
        session.verify = config.verify_ssl
        return session
    
    def _maybe_cleanup(self):
        """Remove idle clients periodically."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        
        self._last_cleanup = now
        
        expired = [
            key for key, info in self._sessions.items()
            if info.idle_seconds > self._max_idle_time
        ]
        
        for key in expired:
            info = self._sessions.pop(key, None)
            if info:
                try:
                    info.client.close()
                except Exception:
                    pass
    
    def _evict_oldest(self):
        """Remove the oldest client to make room."""
        if not self._sessions:
            return
        
        oldest_key = min(
            self._sessions.keys(),
            key=lambda k: self._sessions[k].last_used
        )
        
        info = self._sessions.pop(oldest_key, None)
        if info:
            try:
                info.client.close()
            except Exception:
                pass
    
    def close(self):
        """Close all pooled sessions."""
        with self._lock:
            for info in self._sessions.values():
                try:
                    info.client.close()
                except Exception:
                    pass
            self._sessions.clear()
    
    def stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        total_requests = self._hits + self._misses
        return {
            "active_clients": len(self._sessions),
            "total_requests": total_requests,
            "cache_hits": self._hits,
            "cache_misses": self._misses,
            "hit_rate": self._hits / total_requests if total_requests > 0 else 0,
        }


# =============================================================================
# Global Pool Instances
# =============================================================================

_async_pool: Optional[AsyncConnectionPool] = None
_async_pool_lock = asyncio.Lock()

_sync_pool: Optional[SyncConnectionPool] = None
_sync_pool_lock = threading.Lock()


async def get_async_pool() -> AsyncConnectionPool:
    """Get the global async connection pool (creates if needed)."""
    global _async_pool
    if _async_pool is None:
        async with _async_pool_lock:
            if _async_pool is None:
                _async_pool = AsyncConnectionPool()
    return _async_pool


def get_sync_pool() -> SyncConnectionPool:
    """Get the global sync connection pool (creates if needed)."""
    global _sync_pool
    if _sync_pool is None:
        with _sync_pool_lock:
            if _sync_pool is None:
                _sync_pool = SyncConnectionPool()
    return _sync_pool


async def get_pooled_client(
    base_url: str,
    config: "HttpConfig" = None,
    http2: bool = False,
) -> "AsyncHttpClient":
    """
    Get a pooled async HTTP client for the given base URL.
    
    Returns AsyncHttpClient with full features:
    - Automatic retry with exponential backoff
    - Circuit breaker (shared per base_url)
    - Tracing spans (if context active)
    - Connection reuse
    
    Do NOT close the returned client - the pool manages lifecycle.
    
    Args:
        base_url: Base URL for the client
        config: Optional HttpConfig for timeout/retry settings
        http2: Enable HTTP/2 (default False, requires httpx[http2])
        
    Returns:
        AsyncHttpClient ready to use
        
    Example:
        client = await get_pooled_client("https://api.digitalocean.com")
        client.set_bearer_token("xxx")
        response = await client.get("/v2/droplets")
    """
    pool = await get_async_pool()
    return await pool.get_client(base_url, config, http2)


def get_pooled_sync_client(
    base_url: str,
    config: "HttpConfig" = None,
) -> "SyncHttpClient":
    """
    Get a pooled sync HTTP client for the given base URL.
    
    Returns SyncHttpClient with full features:
    - Automatic retry with exponential backoff
    - Circuit breaker (shared per base_url)
    - Tracing spans (if context active)
    - Connection reuse
    
    Do NOT close the returned client - the pool manages lifecycle.
    
    Args:
        base_url: Base URL for the client
        config: Optional HttpConfig for timeout/retry settings
        
    Returns:
        SyncHttpClient ready to use
        
    Example:
        client = get_pooled_sync_client("https://api.stripe.com/v1")
        client.set_auth_header("Basic", credentials)
        response = client.post("/products", data=form_data)
    """
    pool = get_sync_pool()
    return pool.get_client(base_url, config)


async def close_pool():
    """Close all connection pools. Call on app shutdown."""
    global _async_pool, _sync_pool
    
    if _async_pool:
        await _async_pool.close()
        _async_pool = None
    
    if _sync_pool:
        _sync_pool.close()
        _sync_pool = None


def close_sync_pool():
    """Close sync connection pool only (for sync apps)."""
    global _sync_pool
    if _sync_pool:
        _sync_pool.close()
        _sync_pool = None


def get_pool_stats() -> Dict[str, Any]:
    """Get connection pool statistics."""
    result = {
        "async": _async_pool.stats() if _async_pool else {"active_clients": 0, "message": "Pool not initialized"},
        "sync": _sync_pool.stats() if _sync_pool else {"active_clients": 0, "message": "Pool not initialized"},
    }
    return result


# =============================================================================
# Backwards Compatibility
# =============================================================================

# Alias for backwards compatibility
ConnectionPool = AsyncConnectionPool


async def get_pool() -> AsyncConnectionPool:
    """Get the global connection pool (creates if needed). Alias for get_async_pool."""
    return await get_async_pool()
