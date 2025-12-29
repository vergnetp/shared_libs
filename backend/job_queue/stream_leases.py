# job_queue/stream_leases.py
import uuid
from dataclasses import dataclass
from typing import Optional

from redis import WatchError

from .config.redis_config import QueueRedisConfig


@dataclass
class StreamLeaseConfig:
    """
    Policy defaults live here (infra).
    Apps may override by passing a config instance to StreamLeaseLimiter.
    """
    limit: int = 5              # sensible default
    ttl_seconds: int = 180      # crash-recovery window
    key_namespace: str = "stream_leases"
    key_ttl_grace: int = 60     # keep zset around a bit longer than ttl


class StreamLeaseLimiter:
    """
    Lease-based concurrent stream limiter using a per-user ZSET.

    Key:
      <key_prefix><key_namespace>:<user_id>

    Member:
      lease_id (uuid hex)

    Score:
      expires_at (epoch seconds, based on Redis server time)

    NOTE:
      Uses redis-py sync client (consistent with job_queue). From async FastAPI,
      call via asyncio.to_thread(...).
    """

    def __init__(self, redis_config: QueueRedisConfig, cfg: Optional[StreamLeaseConfig] = None):
        self.redis_config = redis_config
        self.cfg = cfg or StreamLeaseConfig()

    def _key(self, user_id: str) -> str:
        prefix = getattr(self.redis_config, "key_prefix", "") or ""
        if prefix and not prefix.endswith(":"):
            prefix += ":"
        return f"{prefix}{self.cfg.key_namespace}:{user_id}"

    @staticmethod
    def _redis_now(r) -> float:
        sec, usec = r.time()  # Redis server time (avoids clock skew across app servers)
        return float(sec) + (float(usec) / 1_000_000.0)

    def acquire_stream_lease(self, user_id: str) -> Optional[str]:
        """
        Try to acquire a lease for user_id.
        Returns lease_id if allowed, else None.
        """
        if self.cfg.limit <= 0:
            return None

        r = self.redis_config.get_client()
        key = self._key(user_id)
        ttl = int(self.cfg.ttl_seconds)
        grace = int(self.cfg.key_ttl_grace)

        lease_id = uuid.uuid4().hex

        pipe = r.pipeline(transaction=True)
        try:
            for _ in range(10):  # retry on WATCH contention
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
                    # key changed between WATCH and EXEC; retry
                    continue
        finally:
            pipe.reset()

        return None

    def release_stream_lease(self, user_id: str, lease_id: str) -> None:
        """
        Release a previously acquired lease. Safe if expired/removed already.
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

        if remaining <= 0:
            r.delete(key)

    def refresh_stream_lease(self, user_id: str, lease_id: str) -> bool:
        """
        Optional: extend lease while streaming continues.
        Returns False if lease doesn't exist (expired/removed).
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
        Returns number of active (non-expired) leases after cleaning expired ones.
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
