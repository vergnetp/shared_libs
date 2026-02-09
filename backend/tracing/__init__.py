"""
tracing - Lightweight, storage-agnostic span tracing.

Provides structured span tracking with automatic parent/child relationships
via contextvars. Any module can create spans; storage is handled by a
registered callback (typically wired by app_kernel to save to the app DB).

Usage in shared_libs modules (databases, http_client, ai, etc.):

    from tracing import trace_span

    async def fetch_all(self, query, params=None):
        with trace_span("db.fetch_all", query=query[:100]):
            return await self._execute(query, params)

Usage in app code:

    from tracing import trace_span, trace_async

    with trace_span("process_payment", amount=99.99):
        charge = await stripe.charge(amount)

    # Or as decorator
    @trace_async("send_email")
    async def send_email(to, subject):
        ...

Wiring (done by app_kernel at startup):

    from tracing import set_span_callback

    async def save_span(span_data: dict):
        await db.save_entity("kernel_traces", span_data)

    set_span_callback(save_span)
"""

from .core import (
    trace_span,
    trace_async,
    trace_sync,
    start_span,
    end_span,
    get_current_span,
    get_current_trace_id,
    set_span_callback,
    set_span_filter,
    SpanData,
)

__all__ = [
    "trace_span",
    "trace_async",
    "trace_sync",
    "start_span",
    "end_span",
    "get_current_span",
    "get_current_trace_id",
    "set_span_callback",
    "set_span_filter",
    "SpanData",
]
