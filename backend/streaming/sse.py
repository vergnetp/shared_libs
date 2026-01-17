"""
SSE Response Helpers - FastAPI streaming response utilities.

Provides easy-to-use helpers for creating SSE responses that subscribe
to Redis Pub/Sub channels.

Usage:
    from streaming import sse_response, StreamContext
    
    @router.post("/deploy")
    async def deploy(request: DeployRequest):
        ctx = StreamContext.create(...)
        
        # Enqueue background work
        queue_manager.enqueue(...)
        
        # Return SSE stream (subscribes to Redis Pub/Sub)
        return await sse_response(ctx.channel_id)
"""

from __future__ import annotations
import json
import asyncio
from typing import Optional, AsyncIterator, Callable, Any, Dict

from starlette.responses import StreamingResponse

from .events import StreamEvent, EventType
from .channels import AsyncStreamChannel, ChannelConfig, get_async_channel


async def sse_generator(
    channel_id: str,
    channel: Optional[AsyncStreamChannel] = None,
    timeout: Optional[float] = None,
) -> AsyncIterator[str]:
    """
    Async generator that yields SSE events from a Redis Pub/Sub channel.
    
    Automatically handles:
    - Converting events to SSE format
    - Keepalive pings
    - Stream termination on "done" event
    
    Args:
        channel_id: Channel to subscribe to
        channel: Optional AsyncStreamChannel (uses global if not provided)
        timeout: Override subscribe timeout
        
    Yields:
        SSE-formatted strings ("data: {...}\n\n")
    """
    if channel is None:
        channel = get_async_channel()
    
    async for event in channel.subscribe(channel_id, timeout):
        yield event.to_sse()
        
        # Stop on done event
        if event.type == EventType.DONE.value:
            break


async def sse_response(
    channel_id: str,
    channel: Optional[AsyncStreamChannel] = None,
    headers: Optional[Dict[str, str]] = None,
) -> StreamingResponse:
    """
    Create a FastAPI StreamingResponse for SSE.
    
    This is the main entry point for SSE streaming in routes.
    The response subscribes to a Redis Pub/Sub channel and yields
    events as they arrive.
    
    Args:
        channel_id: Channel to subscribe to
        channel: Optional AsyncStreamChannel
        headers: Optional additional headers
        
    Returns:
        StreamingResponse configured for SSE
        
    Example:
        @router.post("/deploy")
        async def deploy(request: DeployRequest):
            ctx = StreamContext.create(...)
            queue_manager.enqueue(entity={"ctx": ctx.to_dict()}, processor=task)
            return await sse_response(ctx.channel_id)
    """
    default_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    
    if headers:
        default_headers.update(headers)
    
    return StreamingResponse(
        sse_generator(channel_id, channel),
        media_type="text/event-stream",
        headers=default_headers,
    )


async def sse_response_with_lease(
    channel_id: str,
    user_id: str,
    channel: Optional[AsyncStreamChannel] = None,
    headers: Optional[Dict[str, str]] = None,
) -> StreamingResponse:
    """
    Create SSE response with automatic lease management.
    
    Acquires a stream lease before starting and releases on completion.
    Raises StreamLimitExceeded if user has too many concurrent streams.
    
    Args:
        channel_id: Channel to subscribe to
        user_id: User ID for lease
        channel: Optional AsyncStreamChannel
        headers: Optional additional headers
        
    Returns:
        StreamingResponse with lease management
        
    Raises:
        StreamLimitExceeded: If user has too many concurrent streams
    """
    from .lifecycle import stream_lease, StreamLimitExceeded
    
    async def generator_with_lease():
        async with stream_lease(user_id) as lease:
            if channel is None:
                _channel = get_async_channel()
            else:
                _channel = channel
            
            refresh_interval = 50  # events
            event_count = 0
            
            async for event in _channel.subscribe(channel_id):
                yield event.to_sse()
                
                event_count += 1
                
                # Refresh lease periodically
                if event_count % refresh_interval == 0:
                    await lease.refresh_async()
                
                if event.type == EventType.DONE.value:
                    break
    
    default_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    
    if headers:
        default_headers.update(headers)
    
    return StreamingResponse(
        generator_with_lease(),
        media_type="text/event-stream",
        headers=default_headers,
    )


# =============================================================================
# Direct Streaming Helpers (for LLM, not queue-based)
# =============================================================================

async def direct_sse_response(
    generator: AsyncIterator[Dict[str, Any]],
    user_id: Optional[str] = None,
    use_lease: bool = True,
    headers: Optional[Dict[str, str]] = None,
) -> StreamingResponse:
    """
    Create SSE response from a direct async generator.
    
    Use this for LLM streaming where you want to stream directly
    without going through Redis Pub/Sub.
    
    Args:
        generator: Async generator yielding dicts to send as SSE
        user_id: User ID for lease (required if use_lease=True)
        use_lease: Whether to use lease limiting
        headers: Optional additional headers
        
    Returns:
        StreamingResponse
        
    Example:
        @router.post("/chat/stream")
        async def chat_stream(request: ChatRequest, user: UserIdentity = Depends(...)):
            async def generate():
                async for token in llm_client.stream(request.prompt):
                    yield {"token": token}
                yield {"done": True}
            
            return await direct_sse_response(
                generate(),
                user_id=str(user.id),
            )
    """
    async def sse_formatter():
        async for data in generator:
            yield f"data: {json.dumps(data, default=str)}\n\n"
    
    async def sse_formatter_with_lease():
        from .lifecycle import stream_lease
        
        async with stream_lease(user_id) as lease:
            event_count = 0
            async for data in generator:
                yield f"data: {json.dumps(data, default=str)}\n\n"
                
                event_count += 1
                if event_count % 50 == 0:
                    await lease.refresh_async()
    
    default_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    
    if headers:
        default_headers.update(headers)
    
    if use_lease and user_id:
        gen = sse_formatter_with_lease()
    else:
        gen = sse_formatter()
    
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers=default_headers,
    )


# =============================================================================
# Legacy Compatibility (for gradual migration)
# =============================================================================

class SSEEmitter:
    """
    Legacy-compatible SSE emitter that publishes to Redis.
    
    For gradual migration from the old in-memory queue-based emitter.
    Use StreamContext for new code.
    
    Usage:
        emitter = SSEEmitter(channel_id)
        emitter.log("Starting...")
        emitter.progress(50)
        emitter.complete(success=True)
    """
    
    def __init__(
        self,
        channel_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        import uuid
        self.channel_id = channel_id or str(uuid.uuid4())
        self._context = context or {}
        self._done = False
        
        from .channels import get_sync_channel
        self._channel = get_sync_channel()
    
    def emit(self, event_type: str, **data) -> None:
        """Emit a raw event."""
        if self._done and event_type != "done":
            return
        
        event = StreamEvent(
            type=event_type,
            channel_id=self.channel_id,
            data=data,
            context=self._context,
        )
        self._channel.publish(event)
    
    def log(self, message: str, level: str = "info") -> None:
        """Emit a log event."""
        self.emit("log", message=message, level=level)
    
    def progress(self, percent: int, message: Optional[str] = None) -> None:
        """Emit a progress event."""
        data = {"progress": percent}
        if message:
            data["message"] = message
        self.emit("progress", **data)
    
    def error(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Emit an error event."""
        data = {"message": message}
        if details:
            data["details"] = details
        self.emit("error", **data)
    
    def complete(
        self,
        success: bool,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Emit completion event."""
        data = {"success": success}
        if result:
            data.update(result)
        if error:
            data["error"] = error
        self._done = True
        self.emit("done", **data)
    
    def server_ready(self, ip: str, name: Optional[str] = None) -> None:
        """Emit server ready event."""
        data = {"ip": ip}
        if name:
            data["name"] = name
        self.emit("server_ready", **data)
