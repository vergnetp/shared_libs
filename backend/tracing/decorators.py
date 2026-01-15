"""
Tracing decorators.

Easy-to-use decorators for tracing functions and methods.

Usage:
    from tracing import traced, SpanKind
    
    @traced("fetch_user", SpanKind.DATABASE)
    async def get_user(user_id: str):
        return await db.get(user_id)
    
    # Or auto-generate name from function:
    @traced()
    def calculate_total(items):
        return sum(items)
"""

from __future__ import annotations
import functools
import asyncio
from typing import Callable, Optional, Any, TypeVar, Union

from .context import get_context, Span
from .types import SpanKind, SpanStatus


F = TypeVar('F', bound=Callable[..., Any])


def traced(
    name: Optional[str] = None,
    kind: SpanKind = SpanKind.INTERNAL,
    record_args: bool = False,
    record_result: bool = False,
) -> Callable[[F], F]:
    """
    Decorator to automatically trace function execution.
    
    Creates a span for each function call, recording:
    - Duration
    - Errors (if raised)
    - Optionally: arguments and return value
    
    Works with both sync and async functions.
    
    Args:
        name: Span name (defaults to function name)
        kind: Type of operation
        record_args: Include function arguments in span attributes
        record_result: Include return value in span attributes
        
    Usage:
        @traced("db_query", SpanKind.DATABASE)
        async def get_user(user_id: str):
            ...
        
        @traced()  # Uses function name
        def calculate(x, y):
            ...
    """
    def decorator(func: F) -> F:
        span_name = name or f"{func.__module__}.{func.__qualname__}"
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            ctx = get_context()
            
            # If no context, just run the function
            if ctx is None:
                return await func(*args, **kwargs)
            
            # Build attributes
            attributes = {}
            if record_args:
                # Safely stringify args (truncated)
                attributes["args"] = _safe_repr(args)[:200]
                attributes["kwargs"] = _safe_repr(kwargs)[:200]
            
            with ctx.span(span_name, kind, attributes) as span:
                result = await func(*args, **kwargs)
                
                if record_result:
                    span.set_attribute("result", _safe_repr(result)[:200])
                
                return result
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            ctx = get_context()
            
            # If no context, just run the function
            if ctx is None:
                return func(*args, **kwargs)
            
            # Build attributes
            attributes = {}
            if record_args:
                attributes["args"] = _safe_repr(args)[:200]
                attributes["kwargs"] = _safe_repr(kwargs)[:200]
            
            with ctx.span(span_name, kind, attributes) as span:
                result = func(*args, **kwargs)
                
                if record_result:
                    span.set_attribute("result", _safe_repr(result)[:200])
                
                return result
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore
    
    return decorator


def _safe_repr(obj: Any) -> str:
    """Safely convert object to string representation."""
    try:
        return repr(obj)
    except Exception:
        return f"<{type(obj).__name__}>"


class TracedOperation:
    """
    Context manager for tracing arbitrary code blocks.
    
    Use when you need more control than the @traced decorator.
    
    Usage:
        with TracedOperation("process_batch", SpanKind.INTERNAL) as span:
            span.set_attribute("batch_size", len(items))
            for item in items:
                process(item)
            span.set_attribute("processed", len(items))
    """
    
    def __init__(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict = None,
    ):
        self.name = name
        self.kind = kind
        self.attributes = attributes
        self._span: Optional[Span] = None
        self._ctx_manager = None
    
    def __enter__(self) -> Span:
        ctx = get_context()
        if ctx is None:
            # Create a dummy span that does nothing
            self._span = Span(name=self.name, kind=self.kind)
            return self._span
        
        self._ctx_manager = ctx.span(self.name, self.kind, self.attributes)
        self._span = self._ctx_manager.__enter__()
        return self._span
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._ctx_manager is not None:
            return self._ctx_manager.__exit__(exc_type, exc_val, exc_tb)
        # Dummy span - just end it
        if exc_type is not None and self._span:
            self._span.record_error(exc_val)
        if self._span:
            self._span.end()
        return None
    
    async def __aenter__(self) -> Span:
        return self.__enter__()
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return self.__exit__(exc_type, exc_val, exc_tb)
