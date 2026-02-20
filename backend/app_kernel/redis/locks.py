"""
Distributed locks using the shared Redis client.

Provides a simple lock primitive that works across multiple workers / 
processes behind a load balancer (when backed by real Redis) and degrades
gracefully to in-memory locks in dev (fakeredis / single-process).

Usage:
    from app_kernel import acquire_lock, release_lock, auto_renew_lock
    
    lock_id = await acquire_lock("deploy:svc-123:prod", ttl=300, holder="user-456")
    if not lock_id:
        raise Exception("Already locked")
    
    renewer = await auto_renew_lock("deploy:svc-123:prod", lock_id)
    try:
        ... # long operation
    finally:
        renewer.cancel()
        await release_lock("deploy:svc-123:prod", lock_id)

Lock identity:
    - key     = caller-defined string (e.g. "deploy:{service_id}:{env}")
    - lock_id = UUID — unique per acquire, used for ownership verification
    - holder  = caller-defined metadata for logging (e.g. user_id)
"""

import asyncio
import uuid
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

LOCK_PREFIX = "lock:"


def _redis_key(key: str) -> str:
    return f"{LOCK_PREFIX}{key}"


async def _get_lock_data(redis, rkey: str) -> Optional[dict]:
    """Read and parse lock value. Returns parsed dict or None."""
    try:
        raw = await redis.get(rkey)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return json.loads(raw)
    except (json.JSONDecodeError, Exception):
        return None


async def acquire_lock(
    key: str,
    ttl: int = 300,
    holder: str = "",
) -> Optional[str]:
    """
    Acquire a distributed lock.
    
    Args:
        key: Lock identifier (e.g. "deploy:svc-123:prod")
        ttl: Lock TTL in seconds. Use auto_renew_lock for long operations.
        holder: Informational — who triggered (user_id, "scale", etc.)
    
    Returns:
        lock_id (UUID) if acquired, None if already locked.
    """
    from .client import get_redis
    
    redis = get_redis()
    if not redis:
        logger.warning(f"Lock acquire skipped (no Redis): {key}")
        return str(uuid.uuid4())  # No Redis = no contention protection
    
    lock_id = str(uuid.uuid4())
    rkey = _redis_key(key)
    value = json.dumps({"lock_id": lock_id, "holder": holder}).encode()
    
    try:
        # SET NX EX — atomic: only succeeds if key doesn't exist
        acquired = await redis.set(rkey, value, nx=True, ex=ttl)
        if acquired:
            logger.info(f"Lock acquired: {key} by {holder} ({lock_id[:8]})")
            return lock_id
        return None
    except Exception as e:
        logger.error(f"Lock acquire error ({key}): {e}")
        return None


async def renew_lock(key: str, lock_id: str, ttl: int = 300) -> bool:
    """
    Extend lock TTL. Returns False if lock was lost (expired or stolen).
    Uses GET + ownership check + EXPIRE (no Lua/EVAL required).
    """
    from .client import get_redis
    
    redis = get_redis()
    if not redis:
        return True  # No Redis = assume still held
    
    rkey = _redis_key(key)
    try:
        data = await _get_lock_data(redis, rkey)
        if not data or data.get('lock_id') != lock_id:
            return False
        await redis.expire(rkey, ttl)
        return True
    except Exception as e:
        logger.error(f"Lock renew error ({key}): {e}")
        return False


async def release_lock(key: str, lock_id: str) -> bool:
    """
    Release a lock. Only succeeds if lock_id matches (you own the lock).
    Uses GET + ownership check + DEL (no Lua/EVAL required).
    """
    from .client import get_redis
    
    redis = get_redis()
    if not redis:
        return True  # No Redis = nothing to release
    
    rkey = _redis_key(key)
    try:
        data = await _get_lock_data(redis, rkey)
        if not data or data.get('lock_id') != lock_id:
            return False
        await redis.delete(rkey)
        logger.info(f"Lock released: {key} ({lock_id[:8]})")
        return True
    except Exception as e:
        logger.error(f"Lock release error ({key}): {e}")
        return False


async def auto_renew_lock(
    key: str,
    lock_id: str,
    ttl: int = 300,
    interval: int = 120,
) -> asyncio.Task:
    """
    Background task that periodically renews a lock.
    
    Cancel the returned task when the operation completes.
    
    Usage:
        lock_id = await acquire_lock("deploy:svc-123:prod", ttl=600)
        renewer = await auto_renew_lock("deploy:svc-123:prod", lock_id, ttl=600)
        try:
            ... # long operation  
        finally:
            renewer.cancel()
            await release_lock("deploy:svc-123:prod", lock_id)
    """

    async def _renew_loop():
        try:
            while True:
                await asyncio.sleep(interval)
                ok = await renew_lock(key, lock_id, ttl)
                if not ok:
                    logger.error(f"Lock auto-renewal failed: {key}")
                    break
        except asyncio.CancelledError:
            pass

    return asyncio.create_task(_renew_loop())
