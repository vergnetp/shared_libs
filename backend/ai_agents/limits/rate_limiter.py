"""Rate limiting with optional Redis backend."""

import time
import asyncio
from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Optional
from dataclasses import dataclass


@dataclass
class RateLimitConfig:
    """Rate limit configuration for a provider/model."""
    rpm: int = 60          # Requests per minute
    tpm: int = 100_000     # Tokens per minute
    rpd: int = 0           # Requests per day (0 = unlimited)
    tpd: int = 0           # Tokens per day (0 = unlimited)


class RateLimiterBackend(ABC):
    """Abstract backend for rate limiting storage."""
    
    @abstractmethod
    async def get_request_count(self, key: str, window_seconds: int = 60) -> int:
        """Get request count in sliding window."""
        pass
    
    @abstractmethod
    async def get_token_count(self, key: str, window_seconds: int = 60) -> int:
        """Get token count in sliding window."""
        pass
    
    @abstractmethod
    async def record_request(self, key: str, tokens: int):
        """Record a request with token count."""
        pass
    
    @abstractmethod
    async def clear(self, key: str):
        """Clear rate limit data for key."""
        pass


class InMemoryBackend(RateLimiterBackend):
    """
    In-memory rate limiter backend using sliding window.
    
    Good for single-process deployments. State lost on restart.
    """
    
    def __init__(self):
        # {key: deque of (timestamp, tokens)}
        self._data: dict[str, deque] = {}
        self._lock = asyncio.Lock()
    
    def _clean_old(self, key: str, window_seconds: int = 60):
        """Remove entries older than window."""
        if key not in self._data:
            return
        
        cutoff = time.time() - window_seconds
        while self._data[key] and self._data[key][0][0] < cutoff:
            self._data[key].popleft()
    
    async def get_request_count(self, key: str, window_seconds: int = 60) -> int:
        async with self._lock:
            self._clean_old(key, window_seconds)
            return len(self._data.get(key, []))
    
    async def get_token_count(self, key: str, window_seconds: int = 60) -> int:
        async with self._lock:
            self._clean_old(key, window_seconds)
            return sum(t[1] for t in self._data.get(key, []))
    
    async def record_request(self, key: str, tokens: int):
        async with self._lock:
            if key not in self._data:
                self._data[key] = deque()
            self._data[key].append((time.time(), tokens))
    
    async def clear(self, key: str):
        async with self._lock:
            if key in self._data:
                del self._data[key]


class RedisBackend(RateLimiterBackend):
    """
    Redis-backed rate limiter using sorted sets.
    
    Good for multi-process/multi-server deployments.
    Requires your queue module's QueueRedisConfig.
    
    Example:
        from processing.queue import QueueRedisConfig
        
        redis_config = QueueRedisConfig(url="redis://localhost:6379/0")
        backend = RedisBackend(redis_config)
        limiter = RateLimiter(backend=backend)
    """
    
    def __init__(self, redis_config: Any):
        """
        Args:
            redis_config: QueueRedisConfig instance from your queue module
        """
        self._config = redis_config
        self._client = None
    
    async def _ensure_client(self):
        """Lazily initialize Redis client."""
        if self._client is None:
            if hasattr(self._config, 'client') and self._config.client:
                self._client = self._config.client
            elif hasattr(self._config, 'url') and self._config.url:
                import redis.asyncio as aioredis
                self._client = aioredis.from_url(self._config.url)
            else:
                raise ValueError("Redis config must have url or client")
        return self._client
    
    def _requests_key(self, key: str) -> str:
        return f"ratelimit:req:{key}"
    
    def _tokens_key(self, key: str) -> str:
        return f"ratelimit:tok:{key}"
    
    async def get_request_count(self, key: str, window_seconds: int = 60) -> int:
        client = await self._ensure_client()
        cutoff = time.time() - window_seconds
        
        # Remove old entries and count remaining
        await client.zremrangebyscore(self._requests_key(key), 0, cutoff)
        return await client.zcard(self._requests_key(key))
    
    async def get_token_count(self, key: str, window_seconds: int = 60) -> int:
        client = await self._ensure_client()
        cutoff = time.time() - window_seconds
        
        # Get all entries in window
        await client.zremrangebyscore(self._tokens_key(key), 0, cutoff)
        entries = await client.zrangebyscore(
            self._tokens_key(key), cutoff, "+inf", withscores=True
        )
        
        # Sum token values (stored in member name as "timestamp:tokens")
        total = 0
        for member, _ in entries:
            if isinstance(member, bytes):
                member = member.decode()
            parts = member.split(":")
            if len(parts) >= 2:
                total += int(parts[1])
        return total
    
    async def record_request(self, key: str, tokens: int):
        client = await self._ensure_client()
        now = time.time()
        
        # Use pipeline for atomic operations
        pipe = client.pipeline()
        
        # Add to requests sorted set (score = timestamp)
        pipe.zadd(self._requests_key(key), {str(now): now})
        pipe.expire(self._requests_key(key), 120)  # 2 min TTL
        
        # Add to tokens sorted set (member = "timestamp:tokens", score = timestamp)
        pipe.zadd(self._tokens_key(key), {f"{now}:{tokens}": now})
        pipe.expire(self._tokens_key(key), 120)
        
        await pipe.execute()
    
    async def clear(self, key: str):
        client = await self._ensure_client()
        await client.delete(self._requests_key(key), self._tokens_key(key))


class RateLimiter:
    """
    Rate limiter for LLM API calls.
    
    Supports RPM (requests per minute) and TPM (tokens per minute).
    Uses in-memory backend by default, Redis optional.
    
    Example (in-memory):
        limiter = RateLimiter(rpm=60, tpm=100000)
        
        # Before each request
        wait_time = await limiter.check("openai:gpt-4o", estimated_tokens=1000)
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        
        # After request
        await limiter.record("openai:gpt-4o", actual_tokens=1500)
    
    Example (Redis):
        from processing.queue import QueueRedisConfig
        
        redis_config = QueueRedisConfig(url="redis://localhost:6379/0")
        limiter = RateLimiter(rpm=60, tpm=100000, backend=RedisBackend(redis_config))
    """
    
    def __init__(
        self,
        rpm: int = 60,
        tpm: int = 100_000,
        backend: RateLimiterBackend = None,
    ):
        self.rpm = rpm
        self.tpm = tpm
        self._backend = backend or InMemoryBackend()
    
    async def check(self, key: str, estimated_tokens: int = 0) -> float:
        """
        Check if request would exceed limits.
        
        Args:
            key: Rate limit key (e.g., "openai:gpt-4o" or "tenant:123:anthropic")
            estimated_tokens: Estimated tokens for this request
            
        Returns:
            Seconds to wait (0 if can proceed immediately)
        """
        # Check RPM
        request_count = await self._backend.get_request_count(key)
        if request_count >= self.rpm:
            # Need to wait for oldest request to expire
            return 60.0 / self.rpm  # Rough estimate
        
        # Check TPM
        if estimated_tokens > 0:
            token_count = await self._backend.get_token_count(key)
            if token_count + estimated_tokens > self.tpm:
                # Need to wait for tokens to free up
                return 60.0 * (estimated_tokens / self.tpm)
        
        return 0.0
    
    async def record(self, key: str, tokens: int):
        """Record a completed request."""
        await self._backend.record_request(key, tokens)
    
    async def wait_if_needed(self, key: str, estimated_tokens: int = 0) -> float:
        """
        Wait if approaching limits.
        
        Args:
            key: Rate limit key
            estimated_tokens: Estimated tokens for this request
            
        Returns:
            Seconds waited
        """
        wait_time = await self.check(key, estimated_tokens)
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        return wait_time
    
    async def acquire(self, key: str, estimated_tokens: int = 0) -> bool:
        """
        Try to acquire a rate limit slot.
        
        Waits if needed, then records the request.
        
        Returns:
            True (always succeeds after waiting)
        """
        await self.wait_if_needed(key, estimated_tokens)
        await self.record(key, estimated_tokens)
        return True


# Provider-specific rate limits
PROVIDER_LIMITS = {
    "openai": {
        "gpt-4o": RateLimitConfig(rpm=500, tpm=30_000),
        "gpt-4o-mini": RateLimitConfig(rpm=500, tpm=200_000),
        "gpt-4-turbo": RateLimitConfig(rpm=500, tpm=30_000),
    },
    "anthropic": {
        "claude-sonnet-4-20250514": RateLimitConfig(rpm=50, tpm=40_000),
        "claude-3-5-sonnet-20241022": RateLimitConfig(rpm=50, tpm=40_000),
        "claude-3-opus-20240229": RateLimitConfig(rpm=50, tpm=40_000),
        "claude-3-haiku-20240307": RateLimitConfig(rpm=50, tpm=100_000),
    },
    "ollama": {
        # Local, no limits
        "*": RateLimitConfig(rpm=999_999, tpm=999_999_999),
    },
}


def get_rate_limiter(
    provider: str,
    model: str,
    backend: RateLimiterBackend = None,
) -> RateLimiter:
    """
    Get rate limiter configured for a specific provider/model.
    
    Args:
        provider: Provider name (openai, anthropic, ollama)
        model: Model name
        backend: Optional Redis backend (uses in-memory if not provided)
    """
    provider_limits = PROVIDER_LIMITS.get(provider, {})
    
    # Check for exact model match
    config = provider_limits.get(model)
    
    # Fall back to wildcard
    if not config:
        config = provider_limits.get("*", RateLimitConfig())
    
    return RateLimiter(rpm=config.rpm, tpm=config.tpm, backend=backend)
