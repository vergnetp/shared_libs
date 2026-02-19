"""
Redis — shared client, fakeredis fallback, and distributed locks.

Client:
    from app_kernel import get_redis, get_sync_redis, is_redis_fake
    
    redis = get_redis()
    await redis.set("key", b"value")
    await redis.get("key")

Locks:
    from app_kernel import acquire_lock, release_lock, auto_renew_lock
    
    lock_id = await acquire_lock("deploy:svc-123:prod", ttl=300)
    renewer = await auto_renew_lock("deploy:svc-123:prod", lock_id)
    try:
        ...
    finally:
        renewer.cancel()
        await release_lock("deploy:svc-123:prod", lock_id)
"""

from .client import (
    init_redis,
    get_redis,
    get_sync_redis,
    is_fake,
    is_fake_url,
    get_url,
)

from .locks import (
    acquire_lock,
    release_lock,
    renew_lock,
    auto_renew_lock,
)

__all__ = [
    # Client
    "init_redis",
    "get_redis",
    "get_sync_redis",
    "is_fake",
    "is_fake_url",
    "get_url",
    # Locks
    "acquire_lock",
    "release_lock",
    "renew_lock",
    "auto_renew_lock",
]
