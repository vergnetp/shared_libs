"""
Redis client management — singleton async + sync clients.

Extracted from dev_deps.py which mixed infrastructure provisioning (Docker 
auto-start) with production client creation. This module handles ONLY client
lifecycle. Dev fallback logic (fakeredis) is preserved here because it's 
tightly coupled to client creation.

Usage:
    # During bootstrap (called once at startup)
    from app_kernel.redis.client import init_redis
    init_redis("redis://localhost:6379")
    
    # Anywhere in the app
    from app_kernel.redis.client import get_redis, get_sync_redis, is_fake
    
    redis = get_redis()          # Async client (or None)
    sync = get_sync_redis()      # Sync client (or None)  
    if not is_fake():
        print("Using real Redis")
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================================
# Module state
# ============================================================================

_url: Optional[str] = None
_async_client = None
_sync_client = None
_is_fake: bool = True
_initialized: bool = False


# ============================================================================
# Fakeredis detection
# ============================================================================

FAKE_URL = "fakeredis://"


def is_fake_url(url: str) -> bool:
    """Check if URL indicates fakeredis."""
    return url is None or url == FAKE_URL or url.startswith("fakeredis://")


# ============================================================================
# Internal: port check (for dev fallback)
# ============================================================================

def _is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """Quick TCP check — used to decide real vs fakeredis."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _parse_redis_url(url: str) -> dict:
    """Parse Redis URL into host/port."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
    }


# ============================================================================
# Initialization
# ============================================================================

def init_redis(url: Optional[str] = None):
    """
    Initialize the Redis subsystem. Called once by bootstrap.
    
    Creates the shared async + sync clients. All subsequent get_redis() /
    get_sync_redis() calls return these singletons.
    
    Args:
        url: Redis URL, None, or "fakeredis://" for in-memory mode.
             For real URLs, tests connectivity and falls back to fakeredis 
             if unreachable (dev-friendly).
    """
    global _url, _async_client, _sync_client, _is_fake, _initialized
    
    _url = url
    _initialized = True
    
    if not url or is_fake_url(url):
        _is_fake = True
        _async_client = _create_fake_async()
        _sync_client = _create_fake_sync()
        logger.info("Redis: using fakeredis (in-memory)")
        return
    
    # Real URL — test connectivity
    try:
        config = _parse_redis_url(url)
        if _is_port_open(config["host"], config["port"]):
            import redis.asyncio as aioredis
            import redis as sync_redis
            
            _async_client = aioredis.from_url(url, decode_responses=False)
            _sync_client = sync_redis.from_url(url, decode_responses=True)
            _is_fake = False
            logger.info(f"Redis: connected to {config['host']}:{config['port']}")
            return
    except Exception as e:
        logger.warning(f"Redis connection failed ({e}), falling back to fakeredis")
    
    # Fallback to fakeredis
    _is_fake = True
    _async_client = _create_fake_async()
    _sync_client = _create_fake_sync()
    logger.info("Redis: using fakeredis (in-memory, fallback)")


# ============================================================================
# Fakeredis factory
# ============================================================================

# Singletons so all callers share one in-memory store
_fakeredis_async = None
_fakeredis_sync = None


def _create_fake_async():
    """Get or create the fakeredis async singleton."""
    global _fakeredis_async
    if _fakeredis_async is None:
        import fakeredis.aioredis
        _fakeredis_async = fakeredis.aioredis.FakeRedis(decode_responses=False)
    return _fakeredis_async


def _create_fake_sync(decode_responses: bool = True):
    """Get or create the fakeredis sync singleton."""
    global _fakeredis_sync
    if _fakeredis_sync is None:
        import fakeredis as _fakeredis
        _fakeredis_sync = _fakeredis.FakeRedis(decode_responses=decode_responses)
    return _fakeredis_sync


# ============================================================================
# Public accessors
# ============================================================================

def get_redis():
    """
    Get the shared async Redis client.
    
    Returns None if Redis was not configured. The client is either a real
    redis.asyncio client or a fakeredis.aioredis instance (transparent to
    callers — same API).
    
    Usage:
        from app_kernel import get_redis
        
        redis = get_redis()
        if redis:
            await redis.set("key", "value")
            await redis.get("key")
    """
    if not _initialized:
        logger.warning("get_redis() called before init_redis() — returning None")
        return None
    return _async_client


def get_sync_redis():
    """
    Get the shared sync Redis client.
    
    Used by job_queue (QueueManager/QueueWorker use sync Redis).
    Returns None if Redis was not configured.
    """
    if not _initialized:
        logger.warning("get_sync_redis() called before init_redis() — returning None")
        return None
    return _sync_client


def is_fake() -> bool:
    """Check if using fakeredis (in-memory, single-process only)."""
    return _is_fake


def get_url() -> Optional[str]:
    """Get the resolved Redis URL."""
    return _url
