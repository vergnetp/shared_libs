"""
Kernel tracing integration.

Wires the tracing library's callback to save spans into the app DB
(kernel_traces table). Also provides the request middleware span creation.

Setup (called by bootstrap):

    from .observability.tracing import setup_tracing
    setup_tracing()      # Registers callback
    setup_tracing(None)  # Clears callback (shutdown)

Middleware creates root spans for HTTP requests.
Job worker creates root spans for job executions.
Shared libs (databases, http_client, ai) create child spans automatically.
"""

import json
import logging
from typing import Optional

try:
    from tracing import set_span_callback, set_span_filter
    TRACING_AVAILABLE = True
except ImportError:
    TRACING_AVAILABLE = False

logger = logging.getLogger(__name__)

_enabled = False


def setup_tracing(filter_fn=None):
    """
    Register the span callback to save to app DB.

    Args:
        filter_fn: Optional filter function(SpanData) -> bool.
                   If set, only spans passing the filter are saved.
                   Default: save all spans.
    """
    global _enabled
    if not TRACING_AVAILABLE:
        logger.debug("Tracing: tracing library not available")
        return
    
    set_span_callback(_save_span)
    if filter_fn is not None:
        set_span_filter(filter_fn)
    _enabled = True
    logger.info("Tracing: enabled, saving to kernel_traces")


def teardown_tracing():
    """Clear the span callback (shutdown)."""
    global _enabled
    if TRACING_AVAILABLE:
        set_span_callback(None)
        set_span_filter(None)
    _enabled = False


async def _save_span(span_dict: dict):
    """
    Callback: push a completed span to Redis for batch saving by admin worker.
    
    Previously wrote directly to DB (one connection per span). Now uses the same
    Redis → admin_worker pattern as audit and metering events.

    Falls back to direct DB write if Redis is not available.
    Fire-and-forget — errors are logged but never propagate.
    """
    try:
        # Try Redis first (preferred — batched by admin worker)
        try:
            from ..redis import get_redis
            redis_client = get_redis()
            if redis_client is not None:
                import json as _json
                # Serialize metadata to JSON string
                metadata = span_dict.get("metadata")
                if metadata and isinstance(metadata, dict):
                    span_dict["metadata"] = _json.dumps(metadata)
                await redis_client.lpush("admin:trace_events", _json.dumps(span_dict))
                return
        except Exception:
            pass  # Redis unavailable, fall back to direct write
        
        # Fallback: direct DB write (one connection per span)
        from ..db.session import raw_db_context

        metadata = span_dict.pop("metadata", None)
        if metadata:
            span_dict["metadata"] = json.dumps(metadata)

        async with raw_db_context() as db:
            await db.save_entity("kernel_traces", span_dict)
    except Exception as e:
        logger.debug(f"Tracing: failed to save span: {e}")
