"""
Task cancellation registry.

Uses in-memory asyncio.Events for zero-latency cancel signalling.
The task loop checks the event between steps; the cancel endpoint sets it.
"""

import asyncio
from typing import Dict


class TaskCancelled(Exception):
    """Raised when a task is cancelled by user."""
    pass


# Backwards compatibility alias
Cancelled = TaskCancelled


# Active task cancel events
_cancel_events: Dict[str, asyncio.Event] = {}


def register(task_id: str) -> asyncio.Event:
    """Register a cancellable task. Returns the event for advanced usage."""
    event = asyncio.Event()
    _cancel_events[task_id] = event
    return event


def trigger(task_id: str) -> bool:
    """Signal a task to cancel. Returns True if task was found."""
    event = _cancel_events.get(task_id)
    if event:
        event.set()
        return True
    return False


def cleanup(task_id: str):
    """Remove cancel event after task finishes."""
    _cancel_events.pop(task_id, None)


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
