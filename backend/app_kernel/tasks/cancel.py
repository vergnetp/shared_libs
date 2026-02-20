"""
Task cancellation registry.

Uses in-memory asyncio.Events for zero-latency cancel signalling.
The task loop checks the event between steps; the cancel endpoint sets it.

Token forwarding:
    The cancel request can carry auth tokens (X-DO-Token, X-CF-Token, etc.)
    via headers. These are stored alongside the cancel flag and exposed via
    get_tokens() so cleanup callbacks can use them without the original
    request scope.
"""

import asyncio
from typing import Dict, Optional


class TaskCancelled(Exception):
    """Raised when a task is cancelled by user."""
    pass


# Backwards compatibility alias
Cancelled = TaskCancelled


# Active task cancel events
_cancel_events: Dict[str, asyncio.Event] = {}

# Tokens forwarded from the cancel request (task_id -> {header: value})
_cancel_tokens: Dict[str, Dict[str, str]] = {}


def register(task_id: str) -> asyncio.Event:
    """Register a cancellable task. Returns the event for advanced usage."""
    event = asyncio.Event()
    _cancel_events[task_id] = event
    return event


def trigger(task_id: str, tokens: Optional[Dict[str, str]] = None) -> bool:
    """Signal a task to cancel. Returns True if task was found.
    
    Args:
        task_id: The task to cancel.
        tokens: Optional dict of auth tokens from the cancel request headers.
            These are stored and accessible via get_tokens() so cleanup
            callbacks can authenticate against external APIs.
    """
    event = _cancel_events.get(task_id)
    if event:
        if tokens:
            _cancel_tokens[task_id] = tokens
        event.set()
        return True
    return False


def get_tokens(task_id: str) -> Dict[str, str]:
    """Get tokens forwarded from the cancel request.
    
    Returns dict of header->value pairs (e.g. {'X-DO-Token': '...'}).
    Empty dict if no tokens were sent or task not found.
    """
    return _cancel_tokens.get(task_id, {})


def cleanup(task_id: str):
    """Remove cancel event and tokens after task finishes."""
    _cancel_events.pop(task_id, None)
    _cancel_tokens.pop(task_id, None)


def is_active(task_id: str) -> bool:
    """Check if a task is currently running (has a cancel event)."""
    return task_id in _cancel_events


def is_cancelled(task_id: str) -> bool:
    """Check if a task has been cancelled (without raising)."""
    event = _cancel_events.get(task_id)
    return event is not None and event.is_set()


def check(task_id: str):
    """Check if cancelled, raise Cancelled if so.
    Call between task steps for fast bail-out."""
    event = _cancel_events.get(task_id)
    if event and event.is_set():
        raise TaskCancelled(f'Task {task_id} cancelled by user')
