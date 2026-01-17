"""
Infrastructure Streaming - SSE utilities for deployment operations.

Provides two modes:
1. **Redis mode** (recommended): Non-blocking, scales to 1000s of streams
2. **Fallback mode**: In-memory queue, blocks 1 of 40 workers but still works

The fallback is automatic when Redis is unavailable.
"""

from __future__ import annotations
import json
import queue
import logging
import threading
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Union

from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Track if we've logged the fallback warning (avoid spam)
_fallback_warning_logged = False


def _is_redis_available() -> bool:
    """Check if Redis streaming is initialized and available."""
    try:
        from shared_libs.backend.streaming import get_sync_channel
        channel = get_sync_channel()
        # Try to get the Redis client
        channel._get_client()
        return True
    except Exception:
        return False


def _log_fallback_warning():
    """Log warning about fallback mode (once per process)."""
    global _fallback_warning_logged
    if not _fallback_warning_logged:
        logger.warning(
            "⚠️  STREAMING FALLBACK: Redis not available. "
            "Using in-memory queue (blocks 1 of 40 FastAPI workers per stream). "
            "Configure REDIS_URL for production scalability."
        )
        _fallback_warning_logged = True


@dataclass
class SSEEvent:
    """A Server-Sent Event."""
    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    
    def to_sse(self) -> str:
        """Format as SSE string."""
        payload = {"type": self.type, **self.data}
        return f"data: {json.dumps(payload)}\n\n"


class SSEEmitter:
    """
    Thread-safe SSE event emitter.
    
    Modes:
    - Redis available: Publishes to Redis Pub/Sub (non-blocking)
    - Redis unavailable: Uses in-memory queue (blocking fallback)
    """
    
    def __init__(
        self,
        channel_id: Optional[str] = None,
        use_memory: Optional[bool] = None,
    ):
        """
        Initialize emitter.
        
        Args:
            channel_id: Unique channel ID (auto-generated if not provided)
            use_memory: Force in-memory mode. If None, auto-detects Redis.
        """
        import uuid
        self.channel_id = channel_id or str(uuid.uuid4())
        self._logs: List[Dict[str, Any]] = []
        self._done = False
        self._result: Optional[Dict[str, Any]] = None
        
        # Determine mode
        if use_memory is None:
            self._use_redis = _is_redis_available()
        else:
            self._use_redis = not use_memory
        
        if not self._use_redis:
            _log_fallback_warning()
            self._memory_queue: queue.Queue = queue.Queue()
        else:
            self._memory_queue = None
        
        self._channel = None
    
    @property
    def is_redis_mode(self) -> bool:
        """Whether emitter is using Redis (non-blocking)."""
        return self._use_redis
    
    def _get_channel(self):
        """Get Redis channel (lazy load)."""
        if self._channel is None and self._use_redis:
            from shared_libs.backend.streaming import get_sync_channel
            self._channel = get_sync_channel()
        return self._channel
    
    def _publish(self, event_type: str, data: Dict[str, Any]) -> None:
        """Publish event to Redis or memory queue."""
        if self._use_redis:
            try:
                from shared_libs.backend.streaming import StreamEvent
                channel = self._get_channel()
                event = StreamEvent(
                    type=event_type,
                    channel_id=self.channel_id,
                    data=data,
                )
                channel.publish(event)
            except Exception as e:
                # Redis failed mid-stream, can't switch to memory
                logger.error(f"Redis publish failed: {e}")
        else:
            # In-memory fallback
            self._memory_queue.put(SSEEvent(type=event_type, data=data))
    
    def emit(self, event_type: str, **data) -> None:
        """Emit a raw event."""
        if self._done and event_type != "done":
            return
        self._publish(event_type, data)
    
    def log(self, message: str) -> None:
        """Emit a log message."""
        timestamp = datetime.utcnow().isoformat()
        self._logs.append({"message": message, "timestamp": timestamp})
        self.emit("log", message=message)
    
    def progress(self, percent: int, message: Optional[str] = None) -> None:
        """Emit progress update."""
        data = {"progress": percent}
        if message:
            data["message"] = message
        self.emit("progress", **data)
    
    def server_ready(self, ip: str, name: Optional[str] = None) -> None:
        """Emit server ready event."""
        data = {"ip": ip}
        if name:
            data["name"] = name
        self.emit("server_ready", **data)
    
    def error(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Emit error event (doesn't stop stream)."""
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
        """Emit completion event and mark stream done."""
        data = {"success": success}
        if result:
            data.update(result)
        if error:
            data["error"] = error
        self._result = data
        self._done = True
        self.emit("done", **data)
    
    @property
    def logs(self) -> List[Dict[str, Any]]:
        """Get collected logs."""
        return self._logs.copy()
    
    @property
    def result(self) -> Optional[Dict[str, Any]]:
        """Get final result (after complete())."""
        return self._result


class DeploymentEmitter(SSEEmitter):
    """SSE emitter with deployment-specific conveniences."""
    
    def __init__(
        self,
        deployment_id: Optional[str] = None,
        channel_id: Optional[str] = None,
        use_memory: Optional[bool] = None,
    ):
        super().__init__(channel_id, use_memory)
        self.deployment_id = deployment_id
    
    def deploy_start(self, target: str, server_count: int) -> None:
        """Emit deployment start event."""
        self.emit("deploy_start", target=target, servers=server_count)
        self.log(f"🚀 Starting deployment: {target} to {server_count} server(s)")
    
    def deploy_success(self, ip: str, container_name: str, url: Optional[str] = None) -> None:
        """Emit successful deployment to a server."""
        data = {"ip": ip, "container": container_name}
        if url:
            data["url"] = url
        self.emit("deploy_success", **data)
        self.log(f"✅ [{ip}] Deployed {container_name}")
    
    def deploy_failure(self, ip: str, error: str) -> None:
        """Emit failed deployment to a server."""
        self.emit("deploy_failure", ip=ip, error=error)
        self.log(f"❌ [{ip}] Failed: {error}")
    
    def complete_deployment(
        self,
        success: bool,
        servers: List[Dict[str, Any]],
        domain: Optional[str] = None,
        deployment_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Complete the deployment stream."""
        result = {
            "deployment_id": deployment_id or self.deployment_id,
            "servers": servers,
        }
        if domain:
            result["domain"] = domain
        self.complete(success=success, result=result, error=error)


def run_in_thread(
    func: Callable,
    emitter: SSEEmitter,
    *args,
    **kwargs,
) -> threading.Thread:
    """
    Run a function in a background thread.
    
    Returns the thread so caller can optionally join() it.
    """
    def wrapper():
        try:
            func(*args, emitter=emitter, **kwargs)
        except Exception as e:
            logger.exception(f"Worker thread failed: {e}")
            if not emitter._done:
                emitter.complete(success=False, error=str(e))
    
    thread = threading.Thread(target=wrapper, daemon=True)
    thread.start()
    return thread


async def _poll_memory_queue(emitter: SSEEmitter):
    """
    Async generator that polls in-memory queue.
    
    This BLOCKS a FastAPI worker but delivers events to client.
    Used as fallback when Redis is unavailable.
    """
    while True:
        try:
            # Non-blocking check with short timeout
            event = emitter._memory_queue.get(timeout=0.1)
            yield event.to_sse()
            
            if event.type == "done":
                break
        except queue.Empty:
            # No event, yield a comment to keep connection alive
            await asyncio.sleep(0.1)
            
            # Check if we should timeout (5 min max)
            # This prevents infinite loops if worker crashes
            continue


async def _subscribe_redis(channel_id: str):
    """Async generator that subscribes to Redis Pub/Sub."""
    from shared_libs.backend.streaming import sse_generator
    async for event_str in sse_generator(channel_id):
        yield event_str


async def sse_response(
    channel_id_or_emitter: Union[str, SSEEmitter],
    worker_func: Optional[Callable] = None,
    *worker_args,
    **worker_kwargs,
) -> StreamingResponse:
    """
    Create an SSE streaming response with automatic fallback.
    
    Usage patterns:
    
    1. Redis mode (non-blocking):
        ctx = StreamContext.create(...)
        queue_manager.enqueue(...)
        return await sse_response(ctx.channel_id)
    
    2. Fallback mode (blocking, but works without Redis):
        emitter = SSEEmitter()
        return await sse_response(emitter, worker_func, arg1, arg2)
    
    3. Auto-detect (recommended for deploy_api):
        emitter = DeploymentEmitter()
        return await sse_response(emitter, deploy_worker, config)
        # Uses Redis if available, falls back to in-memory otherwise
    
    Args:
        channel_id_or_emitter: Channel ID string or SSEEmitter instance
        worker_func: Function to run (required for fallback mode)
        *worker_args: Args for worker function
        **worker_kwargs: Kwargs for worker function
    
    Returns:
        StreamingResponse with SSE events
    """
    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    
    # Case 1: Direct channel_id (Redis mode only)
    if isinstance(channel_id_or_emitter, str):
        channel_id = channel_id_or_emitter
        
        if not _is_redis_available():
            _log_fallback_warning()
            raise RuntimeError(
                "Redis not available. Use SSEEmitter with worker_func for fallback support."
            )
        
        return StreamingResponse(
            _subscribe_redis(channel_id),
            media_type="text/event-stream",
            headers=headers,
        )
    
    # Case 2: SSEEmitter (supports both modes)
    emitter = channel_id_or_emitter
    
    if emitter.is_redis_mode:
        # Redis mode: start worker in thread, subscribe to Redis
        if worker_func:
            run_in_thread(worker_func, emitter, *worker_args, **worker_kwargs)
        
        return StreamingResponse(
            _subscribe_redis(emitter.channel_id),
            media_type="text/event-stream",
            headers=headers,
        )
    else:
        # Fallback mode: start worker in thread, poll memory queue
        if worker_func is None:
            raise ValueError("worker_func required for fallback mode (no Redis)")
        
        run_in_thread(worker_func, emitter, *worker_args, **worker_kwargs)
        
        return StreamingResponse(
            _poll_memory_queue(emitter),
            media_type="text/event-stream",
            headers=headers,
        )


# Re-export for convenience
try:
    from shared_libs.backend.streaming import (
        StreamEvent,
        StreamContext,
        init_streaming,
    )
except ImportError:
    StreamEvent = None
    StreamContext = None
    init_streaming = None


__all__ = [
    # Core
    "SSEEvent",
    "SSEEmitter",
    "DeploymentEmitter",
    "sse_response",
    "run_in_thread",
    
    # From streaming module
    "StreamEvent",
    "StreamContext",
    "init_streaming",
]
