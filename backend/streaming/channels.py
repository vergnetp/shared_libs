"""
Stream Channels - Redis Pub/Sub for real-time event delivery.

Provides both sync and async interfaces:
- SyncStreamChannel: For background workers (publishers)
- AsyncStreamChannel: For FastAPI (subscribers)

Events flow: Worker → Redis Pub/Sub → FastAPI → SSE → Client

Usage:
    # Publisher (background worker)
    channel = SyncStreamChannel(redis_config)
    channel.publish(StreamEvent.log(channel_id, "Starting..."))
    channel.publish(StreamEvent.done(channel_id, success=True))
    
    # Subscriber (FastAPI)
    channel = AsyncStreamChannel(redis_config)
    async for event in channel.subscribe(channel_id):
        yield event.to_sse()
        if event.type == "done":
            break
"""

from __future__ import annotations
import json
import asyncio
from typing import Optional, AsyncIterator, Iterator, Any, TYPE_CHECKING
from dataclasses import dataclass

from .events import StreamEvent, EventType

if TYPE_CHECKING:
    from redis import Redis
    from redis.asyncio import Redis as AsyncRedis


@dataclass
class ChannelConfig:
    """
    Configuration for stream channels.
    
    Attributes:
        key_prefix: Prefix for Redis channel keys
        subscribe_timeout: Timeout for subscribe operations (seconds)
        ping_interval: Interval for keepalive pings (seconds)
        max_idle_time: Max time to wait for events before closing (seconds)
    """
    key_prefix: str = "stream:"
    subscribe_timeout: float = 1.0
    ping_interval: float = 15.0
    max_idle_time: float = 300.0  # 5 minutes


class SyncStreamChannel:
    """
    Synchronous Redis Pub/Sub channel for publishing events.
    
    Used by background workers to publish events to streams.
    Thread-safe.
    
    Args:
        redis_config: Redis configuration with get_client() method
        config: Channel configuration
    """
    
    def __init__(
        self,
        redis_config=None,
        config: Optional[ChannelConfig] = None,
    ):
        self._redis_config = redis_config
        self.cfg = config or ChannelConfig()
        self._client: Optional['Redis'] = None
    
    def _get_client(self) -> 'Redis':
        """Get or create Redis client."""
        if self._client is None:
            if self._redis_config is None:
                # Try to get from global config
                from .leases import get_lease_limiter
                try:
                    limiter = get_lease_limiter()
                    self._redis_config = limiter.redis_config
                except RuntimeError:
                    raise RuntimeError(
                        "No Redis config provided and global limiter not initialized. "
                        "Either pass redis_config or call init_streaming() first."
                    )
            self._client = self._redis_config.get_client()
        return self._client
    
    def _channel_key(self, channel_id: str) -> str:
        """Get Redis channel key for a stream."""
        return f"{self.cfg.key_prefix}{channel_id}"
    
    def publish(self, event: StreamEvent) -> int:
        """
        Publish an event to a channel.
        
        Args:
            event: StreamEvent to publish
            
        Returns:
            Number of subscribers that received the message
        """
        client = self._get_client()
        channel_key = self._channel_key(event.channel_id)
        
        # Publish serialized event
        message = event.to_json()
        return client.publish(channel_key, message)
    
    def publish_log(self, channel_id: str, message: str, level: str = "info") -> int:
        """Convenience: publish a log event."""
        return self.publish(StreamEvent.log(channel_id, message, level))
    
    def publish_progress(
        self,
        channel_id: str,
        percent: int,
        step: Optional[str] = None,
        message: Optional[str] = None,
    ) -> int:
        """Convenience: publish a progress event."""
        return self.publish(StreamEvent.progress(channel_id, percent, step, message))
    
    def publish_done(
        self,
        channel_id: str,
        success: bool,
        result: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> int:
        """Convenience: publish a completion event."""
        return self.publish(StreamEvent.done(channel_id, success, result, error))
    
    def publish_error(
        self,
        channel_id: str,
        message: str,
        details: Optional[dict] = None,
    ) -> int:
        """Convenience: publish an error event (doesn't close stream)."""
        return self.publish(StreamEvent.error(channel_id, message, details))


class AsyncStreamChannel:
    """
    Asynchronous Redis Pub/Sub channel for subscribing to events.
    
    Used by FastAPI to subscribe to streams and yield SSE events.
    Non-blocking - doesn't hold up FastAPI workers.
    
    Args:
        redis_config: Redis configuration (must have async client support)
        config: Channel configuration
    """
    
    def __init__(
        self,
        redis_config=None,
        config: Optional[ChannelConfig] = None,
    ):
        self._redis_config = redis_config
        self.cfg = config or ChannelConfig()
        self._async_client: Optional['AsyncRedis'] = None
    
    async def _get_async_client(self) -> 'AsyncRedis':
        """Get or create async Redis client."""
        if self._async_client is None:
            if self._redis_config is None:
                from .leases import get_lease_limiter
                try:
                    limiter = get_lease_limiter()
                    self._redis_config = limiter.redis_config
                except RuntimeError:
                    raise RuntimeError(
                        "No Redis config provided and global limiter not initialized."
                    )
            
            # Create async client from URL
            import redis.asyncio as aioredis
            
            url = getattr(self._redis_config, 'url', None) or getattr(self._redis_config, '_url', None)
            if url:
                self._async_client = aioredis.from_url(url, decode_responses=True)
            else:
                raise ValueError("Cannot get Redis URL from config")
        
        return self._async_client
    
    def _channel_key(self, channel_id: str) -> str:
        """Get Redis channel key for a stream."""
        return f"{self.cfg.key_prefix}{channel_id}"
    
    async def subscribe(
        self,
        channel_id: str,
        timeout: Optional[float] = None,
    ) -> AsyncIterator[StreamEvent]:
        """
        Subscribe to a channel and yield events.
        
        Automatically handles:
        - Timeout between messages (sends ping)
        - Max idle time (closes stream)
        - Done event (closes stream)
        
        Args:
            channel_id: Channel to subscribe to
            timeout: Override default subscribe timeout
            
        Yields:
            StreamEvent objects as they arrive
        """
        client = await self._get_async_client()
        channel_key = self._channel_key(channel_id)
        
        pubsub = client.pubsub()
        await pubsub.subscribe(channel_key)
        
        sub_timeout = timeout or self.cfg.subscribe_timeout
        idle_time = 0.0
        last_ping = asyncio.get_event_loop().time()
        
        try:
            while True:
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=sub_timeout,
                    )
                    
                    if message is not None and message.get('type') == 'message':
                        # Reset idle time on real message
                        idle_time = 0.0
                        
                        # Parse and yield event
                        try:
                            data = message['data']
                            if isinstance(data, bytes):
                                data = data.decode('utf-8')
                            event = StreamEvent.from_json(data)
                            yield event
                            
                            # Check for done event
                            if event.type == EventType.DONE.value:
                                break
                        except (json.JSONDecodeError, KeyError) as e:
                            # Invalid message, skip
                            continue
                    else:
                        # No message received
                        idle_time += sub_timeout
                        
                        # Check max idle time
                        if idle_time >= self.cfg.max_idle_time:
                            # Yield timeout event and close
                            yield StreamEvent.done(
                                channel_id,
                                success=False,
                                error="Stream timed out (no activity)",
                            )
                            break
                        
                        # Send ping if interval elapsed
                        now = asyncio.get_event_loop().time()
                        if now - last_ping >= self.cfg.ping_interval:
                            yield StreamEvent.ping(channel_id)
                            last_ping = now
                
                except asyncio.TimeoutError:
                    # Timeout waiting for message - send ping
                    idle_time += sub_timeout
                    
                    if idle_time >= self.cfg.max_idle_time:
                        yield StreamEvent.done(
                            channel_id,
                            success=False,
                            error="Stream timed out",
                        )
                        break
                    
                    now = asyncio.get_event_loop().time()
                    if now - last_ping >= self.cfg.ping_interval:
                        yield StreamEvent.ping(channel_id)
                        last_ping = now
        
        finally:
            # Always unsubscribe and close
            await pubsub.unsubscribe(channel_key)
            await pubsub.close()
    
    async def close(self):
        """Close the async client connection."""
        if self._async_client is not None:
            await self._async_client.close()
            self._async_client = None


# Module-level channels (optional singletons)
_sync_channel: Optional[SyncStreamChannel] = None
_async_channel: Optional[AsyncStreamChannel] = None


def get_sync_channel(redis_config=None) -> SyncStreamChannel:
    """Get or create the sync channel singleton."""
    global _sync_channel
    if _sync_channel is None:
        _sync_channel = SyncStreamChannel(redis_config)
    return _sync_channel


def get_async_channel(redis_config=None) -> AsyncStreamChannel:
    """Get or create the async channel singleton."""
    global _async_channel
    if _async_channel is None:
        _async_channel = AsyncStreamChannel(redis_config)
    return _async_channel


def init_channels(redis_config, config: Optional[ChannelConfig] = None):
    """Initialize both channel singletons."""
    global _sync_channel, _async_channel
    _sync_channel = SyncStreamChannel(redis_config, config)
    _async_channel = AsyncStreamChannel(redis_config, config)
