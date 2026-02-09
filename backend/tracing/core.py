"""
Core span tracing engine.

Uses contextvars for async-safe parent/child span tracking.
Zero dependencies beyond stdlib. Storage is delegated to a callback.

Span lifecycle:
    1. start_span() creates SpanData, pushes onto context stack
    2. Code runs (may create child spans)
    3. end_span() records duration, pops context, fires callback

Callback receives a dict with:
    {
        "trace_id": "abc-123",       # Root span ID (groups all spans in one request/job)
        "span_id": "def-456",        # This span's unique ID
        "parent_id": "abc-123",      # Parent span ID (None for root)
        "name": "db.fetch_all",      # Span name
        "duration_ms": 12.5,         # Wall clock time
        "started_at": "2025-...",    # ISO timestamp
        "ended_at": "2025-...",      # ISO timestamp
        "status": "ok",              # "ok" or "error"
        "error": None,               # Error message if failed
        "error_type": None,          # Exception class name
        "metadata": {"query": "..."} # Arbitrary key-value pairs
    }
"""

import time
import uuid
import asyncio
import logging
import functools
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# ============================================================================
# Context management
# ============================================================================

_current_span: ContextVar[Optional["SpanData"]] = ContextVar("_current_span", default=None)
_span_callback: Optional[Callable[[dict], Awaitable[None]]] = None
_span_filter: Optional[Callable[["SpanData"], bool]] = None


# ============================================================================
# Data
# ============================================================================

@dataclass
class SpanData:
    """A single span representing a unit of work."""
    name: str
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    trace_id: str = ""       # Set from root span or parent
    parent_id: Optional[str] = None
    started_at: str = ""
    ended_at: Optional[str] = None
    duration_ms: float = 0.0
    status: str = "ok"       # "ok" or "error"
    error: Optional[str] = None
    error_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Internal
    _start_time: float = field(default=0.0, repr=False)

    def to_dict(self) -> dict:
        """Convert to dict for callback (excludes internal fields)."""
        d = asdict(self)
        d.pop("_start_time", None)
        return d


# ============================================================================
# Callback registration
# ============================================================================

def set_span_callback(callback: Optional[Callable[[dict], Awaitable[None]]]):
    """
    Register callback invoked when a span ends.

    The callback receives a span dict. It can be sync or async.
    If async, it's scheduled as a task (fire-and-forget) so it
    never blocks the caller.

    Pass None to clear.
    """
    global _span_callback
    _span_callback = callback


def set_span_filter(fn: Optional[Callable[[SpanData], bool]]):
    """
    Register optional filter. If set, callback only fires when fn(span) is True.

    Useful for sampling or only saving slow/error spans:

        set_span_filter(lambda s: s.duration_ms > 100 or s.status == "error")
    """
    global _span_filter
    _span_filter = fn


# ============================================================================
# Span API
# ============================================================================

def get_current_span() -> Optional[SpanData]:
    """Get the current active span (or None)."""
    return _current_span.get()


def get_current_trace_id() -> Optional[str]:
    """Get the current trace ID (root span's ID). Returns None if no active trace."""
    span = _current_span.get()
    return span.trace_id if span else None


def start_span(name: str, **metadata) -> SpanData:
    """
    Start a new span. Automatically links to parent if one exists.

    Args:
        name: Span name (e.g. "db.fetch_all", "http.GET", "process_payment")
        **metadata: Arbitrary key-value pairs attached to the span

    Returns:
        SpanData that must be passed to end_span()
    """
    parent = _current_span.get()
    now = datetime.now(timezone.utc)

    span = SpanData(
        name=name,
        trace_id=parent.trace_id if parent else uuid.uuid4().hex[:16],
        parent_id=parent.span_id if parent else None,
        started_at=now.isoformat(),
        metadata=metadata,
        _start_time=time.perf_counter(),
    )

    _current_span.set(span)
    return span


def end_span(span: SpanData, error: Optional[Exception] = None):
    """
    End a span, record duration, fire callback.

    Args:
        span: The span returned by start_span()
        error: Exception if the span failed
    """
    span.duration_ms = (time.perf_counter() - span._start_time) * 1000
    span.ended_at = datetime.now(timezone.utc).isoformat()

    if error:
        span.status = "error"
        span.error = str(error)
        span.error_type = type(error).__name__

    # Restore parent as current
    # Walk up: if this span has a parent_id, we can't easily restore the
    # parent SpanData object. Instead, we set current to None if this is root,
    # or leave parent in place. The context manager handles this properly.
    # For manual start/end usage, caller manages context.

    _fire_callback(span)


def _fire_callback(span: SpanData):
    """Fire the registered callback if set and filter passes."""
    if _span_callback is None:
        return

    if _span_filter is not None and not _span_filter(span):
        return

    span_dict = span.to_dict()

    try:
        if asyncio.iscoroutinefunction(_span_callback):
            # Schedule as fire-and-forget task
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_safe_callback(span_dict))
            except RuntimeError:
                # No running loop - skip (sync context without async)
                pass
        else:
            _span_callback(span_dict)
    except Exception as e:
        logger.debug(f"Span callback error: {e}")


async def _safe_callback(span_dict: dict):
    """Wrapper to catch callback errors without killing the task."""
    try:
        await _span_callback(span_dict)
    except Exception as e:
        logger.debug(f"Span callback error: {e}")


# ============================================================================
# Context manager (preferred API)
# ============================================================================

@contextmanager
def trace_span(name: str, **metadata):
    """
    Context manager to trace a block of code.

    Usage:
        with trace_span("db.query", table="users"):
            results = await db.fetch_all("SELECT * FROM users")

        with trace_span("http.POST", url="https://api.stripe.com"):
            resp = await client.post(url, data=payload)

    Automatically:
    - Creates span with parent linkage
    - Records duration
    - Captures errors
    - Fires callback
    - Restores parent span context
    """
    parent = _current_span.get()
    span = start_span(name, **metadata)
    token = _current_span.set(span)

    try:
        yield span
    except Exception as e:
        end_span(span, error=e)
        _current_span.reset(token)
        raise
    else:
        end_span(span)
        _current_span.reset(token)


# ============================================================================
# Decorators
# ============================================================================

def trace_async(name: str = None, **default_metadata):
    """
    Decorator for async functions.

    Usage:
        @trace_async("send_email")
        async def send_email(to, subject):
            ...

        @trace_async()  # Uses function name
        async def process_payment(amount):
            ...
    """
    def decorator(fn):
        span_name = name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            with trace_span(span_name, **default_metadata):
                return await fn(*args, **kwargs)

        return wrapper
    return decorator


def trace_sync(name: str = None, **default_metadata):
    """
    Decorator for sync functions.

    Usage:
        @trace_sync("compute_hash")
        def compute_hash(data):
            ...
    """
    def decorator(fn):
        span_name = name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with trace_span(span_name, **default_metadata):
                return fn(*args, **kwargs)

        return wrapper
    return decorator
