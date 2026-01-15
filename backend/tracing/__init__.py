"""
Tracing Module - Request-scoped distributed tracing.

Provides lightweight tracing for correlating operations within a request.
All external HTTP calls, database queries, and internal operations can be
traced and correlated.

Quick Start:
    # In middleware (automatic with app_kernel):
    from tracing import RequestContext, set_context, clear_context
    
    ctx = RequestContext.create(request_id=request.state.request_id)
    set_context(ctx)
    try:
        response = await call_next(request)
    finally:
        ctx.end()
        if ctx.has_errors or ctx.duration_ms > 1000:
            save_traces(ctx)
        clear_context()
    
    # In your code - option 1: context manager
    from tracing import get_context, SpanKind
    
    ctx = get_context()
    with ctx.span("fetch_user", SpanKind.DATABASE) as span:
        span.set_attribute("user_id", user_id)
        user = await db.get_user(user_id)
    
    # Option 2: decorator
    from tracing import traced, SpanKind
    
    @traced("external_api", SpanKind.HTTP_CLIENT)
    async def call_stripe(customer_id: str):
        ...

Key Concepts:
    - RequestContext: Created per request, holds all spans
    - Span: A single traced operation with timing and attributes
    - SpanKind: Type of operation (HTTP, DB, internal, etc.)
    - Context vars: Thread/async-safe storage of current context

Integration Points:
    - http/ module: Auto-creates spans for HTTP calls
    - databases/ module: Can add spans for queries
    - app_kernel middleware: Creates RequestContext per request
"""

from .types import (
    SpanKind,
    SpanStatus,
    SpanAttributes,
)

from .context import (
    Span,
    RequestContext,
    get_context,
    set_context,
    clear_context,
)

from .decorators import (
    traced,
    TracedOperation,
)


__all__ = [
    # Types
    "SpanKind",
    "SpanStatus", 
    "SpanAttributes",
    # Context
    "Span",
    "RequestContext",
    "get_context",
    "set_context",
    "clear_context",
    # Decorators
    "traced",
    "TracedOperation",
]
