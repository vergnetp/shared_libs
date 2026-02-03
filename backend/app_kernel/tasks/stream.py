"""
TaskStream - combines SSE streaming, logging, and cancellation.

Two patterns:

1. Auto-generated task_id (snapshot, rollback):

    stream = TaskStream("snapshot")  # auto-registers
    try:
        yield stream.task_id_event()
        stream("Working...")
        yield stream.log()
        stream.check()
        yield stream.complete(True)
    except Cancelled:
        yield stream.complete(False, error='Cancelled')
    finally:
        stream.cleanup()

2. Known task_id (deploy with DB-generated ID):

    stream = TaskStream(task_id=deployment_id)  # auto-registers with known id
    try:
        yield stream.task_id_event(deployment_id=deployment_id)
        ...

3. Deferred registration (logger first, register later):

    stream = TaskStream(register=False)  # just a logger
    stream("Setting up...")
    yield stream.log()
    # ... create DB record ...
    stream.register(deployment_id)
    yield stream.task_id_event()
"""

import uuid
from typing import List

from datetime import datetime, timezone

from . import cancel
from .cancel import Cancelled
from .sse import sse_task_id, sse_log, sse_complete, sse_event


class TaskStream:
    """Streaming task context with built-in cancel support and SSE formatting."""
    
    def __init__(self, prefix: str = "task", task_id: str = None, register: bool = True):
        self.task_id = task_id or f"{prefix}-{uuid.uuid4().hex[:12]}"
        self._logs: List[str] = []
        self._registered = False
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
        """Emit task_id SSE event. Call once at start, yield the result."""
        return sse_task_id(self.task_id, **extra)
    
    def log(self, level: str = "info") -> str:
        """Emit the last logged message as an SSE log event."""
        if not self._logs:
            return ""
        return sse_log(self._logs[-1], level)
    
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