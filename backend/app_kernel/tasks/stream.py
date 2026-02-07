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

import uuid
from typing import List

from datetime import datetime, timezone

from . import cancel
from .cancel import TaskCancelled, Cancelled
from .sse import sse_task_id, sse_log, sse_complete, sse_event


class TaskStream:
    """Streaming task context with built-in cancel support and SSE formatting."""
    
    def __init__(self, prefix: str = "task", task_id: str = None, register: bool = True):
        self.task_id = task_id or f"{prefix}-{uuid.uuid4().hex[:12]}"
        self._logs: List[str] = []
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
        """Remove cancel registration. Call in finally or use context manager."""
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