"""
Tracing context management.

Provides:
- Span: A single traced operation
- RequestContext: Collection of spans for a request
- Context variables for thread/task-local storage

Usage:
    from tracing import get_context, Span, SpanKind
    
    # In middleware (automatic):
    ctx = RequestContext.create()
    set_context(ctx)
    
    # In your code:
    ctx = get_context()
    with ctx.span("fetch_user", SpanKind.DATABASE) as span:
        user = db.get_user(id)
        span.set_attribute("user_id", id)
    
    # Or manually:
    span = ctx.start_span("http_call", SpanKind.HTTP_CLIENT)
    try:
        response = http.get(url)
        span.set_status(SpanStatus.OK)
    except Exception as e:
        span.record_error(e)
        raise
    finally:
        span.end()
"""

from __future__ import annotations
import time
import uuid
import threading
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Generator, TYPE_CHECKING

from .types import SpanKind, SpanStatus, SpanAttributes


# Context variable for async-safe request context
_request_context: ContextVar[Optional['RequestContext']] = ContextVar(
    'request_context', 
    default=None
)

# Thread-local fallback for sync code not in async context
_thread_local = threading.local()


def get_context() -> Optional['RequestContext']:
    """
    Get current request context.
    
    Returns None if no context is set (e.g., outside request lifecycle).
    """
    # Try context var first (works in async)
    ctx = _request_context.get()
    if ctx is not None:
        return ctx
    
    # Fall back to thread-local for sync code
    return getattr(_thread_local, 'context', None)


def set_context(ctx: 'RequestContext') -> None:
    """
    Set current request context.
    
    Called by middleware at request start.
    """
    _request_context.set(ctx)
    _thread_local.context = ctx


def clear_context() -> None:
    """
    Clear current request context.
    
    Called by middleware at request end.
    """
    _request_context.set(None)
    _thread_local.context = None


@dataclass
class Span:
    """
    A single traced operation.
    
    Represents a unit of work with timing, status, and attributes.
    Can be nested (parent_id references another span).
    
    Usage:
        span = Span(name="fetch_data", kind=SpanKind.HTTP_CLIENT)
        span.set_attribute("url", "https://api.example.com")
        try:
            result = do_work()
            span.set_status(SpanStatus.OK)
        except Exception as e:
            span.record_error(e)
        finally:
            span.end()
    """
    name: str
    kind: SpanKind = SpanKind.INTERNAL
    
    # Identifiers
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    trace_id: Optional[str] = None  # Set by RequestContext
    parent_id: Optional[str] = None
    
    # Timing
    start_time: float = field(default_factory=time.perf_counter)
    end_time: Optional[float] = None
    
    # Status
    status: SpanStatus = SpanStatus.OK
    
    # Attributes
    attributes: SpanAttributes = field(default_factory=SpanAttributes)
    
    # Events (for logging within span)
    events: List[Dict[str, Any]] = field(default_factory=list)
    
    def end(self) -> None:
        """Mark span as complete."""
        if self.end_time is None:
            self.end_time = time.perf_counter()
    
    @property
    def duration_ms(self) -> Optional[float]:
        """Duration in milliseconds."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000
    
    @property
    def is_error(self) -> bool:
        """Check if span ended in error."""
        return self.status in (SpanStatus.ERROR, SpanStatus.TIMEOUT)
    
    def set_status(self, status: SpanStatus) -> None:
        """Set span status."""
        self.status = status
    
    def set_attribute(self, key: str, value: Any) -> None:
        """Set a custom attribute."""
        self.attributes.custom[key] = value
    
    def set_attributes(self, attrs: Dict[str, Any]) -> None:
        """Set multiple custom attributes."""
        self.attributes.custom.update(attrs)
    
    def record_error(self, error: Exception) -> None:
        """Record an error on this span."""
        self.status = SpanStatus.ERROR
        self.attributes.error_type = type(error).__name__
        self.attributes.error_message = str(error)[:500]  # Truncate
        self.add_event("exception", {
            "type": type(error).__name__,
            "message": str(error)[:500],
        })
    
    def add_event(self, name: str, attributes: Dict[str, Any] = None) -> None:
        """Add a timestamped event to this span."""
        self.events.append({
            "name": name,
            "timestamp": time.perf_counter(),
            "attributes": attributes or {},
        })
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert span to dictionary for serialization."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "parent_id": self.parent_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "attributes": self.attributes.to_dict(),
            "events": self.events,
        }
    
    def __enter__(self) -> 'Span':
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None:
            self.record_error(exc_val)
        self.end()


@dataclass
class RequestContext:
    """
    Context for a single request lifecycle.
    
    Collects all spans created during request processing.
    Created by middleware, accessible via get_context().
    
    Usage:
        # Middleware creates context
        ctx = RequestContext.create(request_id="abc123")
        set_context(ctx)
        
        # Code creates spans
        with ctx.span("db_query", SpanKind.DATABASE) as span:
            result = db.query(...)
        
        # Middleware collects spans at end
        spans = ctx.get_spans()
        if ctx.has_errors or ctx.duration_ms > 1000:
            save_to_db(spans)
    """
    # Request identifiers
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    
    # Request metadata
    method: Optional[str] = None
    path: Optional[str] = None
    user_id: Optional[str] = None
    workspace_id: Optional[str] = None
    
    # Timing
    start_time: float = field(default_factory=time.perf_counter)
    end_time: Optional[float] = None
    
    # Collected spans
    _spans: List[Span] = field(default_factory=list)
    _current_span: Optional[Span] = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    
    @classmethod
    def create(
        cls,
        request_id: Optional[str] = None,
        method: Optional[str] = None,
        path: Optional[str] = None,
        user_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> 'RequestContext':
        """Create a new request context."""
        ctx = cls(
            request_id=request_id or uuid.uuid4().hex,
            method=method,
            path=path,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        return ctx
    
    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Dict[str, Any] = None,
    ) -> Span:
        """
        Start a new span.
        
        The span is automatically added to this context.
        Parent is set to current span if one exists.
        
        Args:
            name: Operation name
            kind: Type of operation
            attributes: Initial attributes
            
        Returns:
            New Span instance (must call .end() when done)
        """
        with self._lock:
            parent_id = self._current_span.span_id if self._current_span else None
            
            span = Span(
                name=name,
                kind=kind,
                trace_id=self.trace_id,
                parent_id=parent_id,
            )
            
            if attributes:
                span.set_attributes(attributes)
            
            self._spans.append(span)
            return span
    
    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Dict[str, Any] = None,
    ) -> Generator[Span, None, None]:
        """
        Context manager for creating spans.
        
        Automatically handles:
        - Setting parent span
        - Recording errors
        - Ending span
        
        Usage:
            with ctx.span("fetch_data", SpanKind.HTTP_CLIENT) as span:
                span.set_attribute("url", url)
                result = http.get(url)
        """
        span = self.start_span(name, kind, attributes)
        
        # Set as current span for nesting
        with self._lock:
            previous_span = self._current_span
            self._current_span = span
        
        try:
            yield span
            if span.status == SpanStatus.OK:
                # Only set OK if not already changed
                span.set_status(SpanStatus.OK)
        except Exception as e:
            span.record_error(e)
            raise
        finally:
            span.end()
            with self._lock:
                self._current_span = previous_span
    
    def end(self) -> None:
        """Mark request context as complete."""
        self.end_time = time.perf_counter()
    
    @property
    def duration_ms(self) -> Optional[float]:
        """Total request duration in milliseconds."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000
    
    @property
    def has_errors(self) -> bool:
        """Check if any span has errors."""
        return any(span.is_error for span in self._spans)
    
    def get_spans(self) -> List[Span]:
        """Get all collected spans."""
        with self._lock:
            return list(self._spans)
    
    def get_slow_spans(self, threshold_ms: float = 100) -> List[Span]:
        """Get spans slower than threshold."""
        return [
            span for span in self._spans
            if span.duration_ms and span.duration_ms > threshold_ms
        ]
    
    def get_error_spans(self) -> List[Span]:
        """Get all spans with errors."""
        return [span for span in self._spans if span.is_error]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for serialization."""
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "method": self.method,
            "path": self.path,
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "has_errors": self.has_errors,
            "span_count": len(self._spans),
            "spans": [span.to_dict() for span in self._spans],
        }
    
    def summary(self) -> Dict[str, Any]:
        """Get summary without full span details."""
        return {
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "method": self.method,
            "path": self.path,
            "duration_ms": self.duration_ms,
            "has_errors": self.has_errors,
            "span_count": len(self._spans),
            "error_count": len(self.get_error_spans()),
            "slow_span_count": len(self.get_slow_spans()),
        }
