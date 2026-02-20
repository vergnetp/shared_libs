"""
TaskStream - combines SSE streaming, logging, and cancellation.

Basic usage:

    stream = TaskStream("deploy")
    try:
        yield stream.log("Building...")   # Auto-sends task_id on first call
        stream.check()                    # Raises Cancelled if user cancelled
        
        yield stream.log("Pushing...")
        yield stream.log("Done!")
        
        yield stream.complete(True)
    except TaskCancelled:
        yield stream.complete(False, error='Cancelled')
    finally:
        stream.cleanup()

With known task_id (e.g., from DB):

    stream = TaskStream(task_id=deployment_id)
    yield stream.log("Starting...")  # task_id event uses deployment_id

Deferred registration (logger first, register later):

    stream = TaskStream(register=False)  # just a logger
    yield stream.log("Setting up...")
    # ... create DB record ...
    stream.register(deployment_id)  # now cancellable
"""

import asyncio
import uuid
import logging
from typing import Any, Awaitable, Callable, List

from datetime import datetime, timezone

from . import cancel
from .cancel import TaskCancelled, Cancelled
from .sse import sse_task_id, sse_log, sse_complete, sse_event


_logger = logging.getLogger(__name__)


class _CancelHandle:
    """Handle returned by on_cancel() — call discard() when the resource is committed."""
    __slots__ = ('_entry', '_list')
    
    def __init__(self, entry: list, parent_list: list):
        self._entry = entry
        self._list = parent_list
    
    def discard(self):
        """Remove this cleanup — the resource was committed successfully."""
        try:
            self._list.remove(self._entry)
        except ValueError:
            pass  # already removed


class TaskStream:
    """Streaming task context with built-in cancel support and SSE formatting."""
    
    def __init__(self, prefix: str = "task", task_id: str = None, register: bool = True):
        self.task_id = task_id or f"{prefix}-{uuid.uuid4().hex[:12]}"
        self._logs: List[str] = []
        self._cancel_callbacks: List[list] = []  # [[fn, args, kwargs], ...]
        self._registered = False
        self._task_id_sent = False
        if register:
            self._do_register()
    
    def _do_register(self):
        cancel.register(self.task_id)
        self._registered = True
    
    def register(self, task_id: str = None):
        """Register for cancellation (deferred pattern).
        Optionally override task_id. Returns self for chaining."""
        if task_id:
            self.task_id = task_id
        self._do_register()
        return self
    
    def __call__(self, msg: str):
        """Append a timestamped log message."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self._logs.append(f"[{ts}] {msg}")
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
        return False
    
    def cleanup(self):
        """Remove cancel registration and clear callbacks. Call in finally or use context manager."""
        self._cancel_callbacks.clear()
        if self._registered:
            cancel.cleanup(self.task_id)
            self._registered = False
    
    # --- Cancel ---
    
    def check(self):
        """Check if cancelled, raise Cancelled if so."""
        cancel.check(self.task_id)
    
    @property
    def is_cancelled(self) -> bool:
        """Check if cancelled (without raising)."""
        return cancel.is_cancelled(self.task_id)
    
    async def cancellable(self, coro: Awaitable[Any], interval: float = 0.5) -> Any:
        """Await a coroutine while polling for cancellation.
        
        Long HTTP calls (DO API, image export, docker build) can block for
        minutes. This polls check() every `interval` seconds so cancel
        responds within that window instead of waiting for the call to finish.
        
        The in-flight call continues in background on cancel — the caller's
        except TaskCancelled handler is responsible for cleanup.
        
        Usage:
            # Instead of: result = await provision_droplet(...)
            result = await stream.cancellable(provision_droplet(...))
        """
        task = asyncio.ensure_future(coro)
        while not task.done():
            self.check()  # raises TaskCancelled if flagged
            await asyncio.wait({task}, timeout=interval)
        return task.result()
    
    async def cancellable_gather(self, *coros: Awaitable[Any], interval: float = 0.5) -> List[Any]:
        """Like asyncio.gather() but polls for cancellation while waiting.
        
        Usage:
            results = await stream.cancellable_gather(
                provision_droplet(region='lon1'),
                provision_droplet(region='lon1'),
                provision_droplet(region='lon1'),
            )
        """
        tasks = [asyncio.ensure_future(c) for c in coros]
        while not all(t.done() for t in tasks):
            self.check()
            await asyncio.wait(set(tasks), timeout=interval)
        return [t.result() for t in tasks]
    
    # --- Cancel cleanup registration ---
    
    def on_cancel(self, fn: Callable, *args, **kwargs) -> _CancelHandle:
        """Register a cleanup callback to run if the task is cancelled.
        
        Callbacks run in reverse order (LIFO) — last registered runs first,
        like a stack of undo operations.
        
        Returns a handle — call handle.discard() when the resource is
        committed and no longer needs cleanup on cancel.
        
        Usage:
            # Register cleanup as you create resources:
            stream.on_cancel(destroy_droplet, None, did, do_token)
            stream.on_cancel(agent_client.remove_container, ip, name, token)
            
            # If resource is committed (no undo needed):
            handle = stream.on_cancel(remove_temp_file, path)
            # ... work succeeds ...
            handle.discard()  # committed — skip this on cancel
        """
        entry = [fn, args, kwargs]
        self._cancel_callbacks.append(entry)
        return _CancelHandle(entry, self._cancel_callbacks)
    
    async def run_cleanups(self):
        """Run all registered cancel callbacks in reverse order (LIFO).
        
        Best-effort: each callback runs independently, errors are logged
        but don't prevent remaining callbacks from running.
        
        Call this in your except TaskCancelled handler:
        
            except TaskCancelled:
                stream("Cancelled by user.")
                yield stream.log(level="warning")
                await stream.run_cleanups()
                yield stream.complete(False, error="Cancelled by user")
        """
        for fn, args, kwargs in reversed(self._cancel_callbacks):
            try:
                result = fn(*args, **kwargs)
                if asyncio.iscoroutine(result) or asyncio.isfuture(result):
                    await result
            except Exception as e:
                _logger.warning(f"Cancel cleanup failed ({fn.__name__}): {e}")
        self._cancel_callbacks.clear()
    
    # --- SSE event helpers ---
    
    def task_id_event(self, **extra) -> str:
        """Emit task_id SSE event. Usually not needed - log() auto-sends on first call."""
        self._task_id_sent = True
        return sse_task_id(self.task_id, **extra)
    
    def log(self, message: str = None, level: str = "info") -> str:
        """Emit a log message as an SSE event.
        
        First call automatically prepends the task_id event (unless 
        task_id_event() was already called explicitly).
        
        Args:
            message: Log message. If None, uses last message from __call__().
            level: Log level (info, warn, error).
        
        Example:
            yield stream.log("Building...")
            yield stream.log("Pushing...")
        """
        # If message provided, add it to logs
        if message:
            self(message)  # Calls __call__ to add timestamped message
        
        if not self._logs:
            return ""
        
        result = ""
        if not self._task_id_sent:
            result += sse_task_id(self.task_id)
            self._task_id_sent = True
        
        result += sse_log(self._logs[-1], level)
        return result
    
    def complete(self, success: bool, error: str = None, **extra) -> str:
        """Emit completion SSE event."""
        return sse_complete(success, self.task_id, error, **extra)
    
    def event(self, event_name: str, data: dict) -> str:
        """Emit a custom SSE event."""
        return sse_event(event_name, data)
    
    # --- Log access ---
    
    def flush(self) -> str:
        """Return all logs joined by newlines."""
        return "\n".join(self._logs)
    
    @property
    def last(self) -> str:
        """Return the last log message."""
        return self._logs[-1] if self._logs else ""
    
    @property
    def logs(self) -> List[str]:
        """Direct access to log list (for polling loops)."""
        return self._logs