"""
FastAPI middleware for request tracing.

Automatically creates a RequestContext for each incoming request and
captures timing, status code, user info, etc.
"""

from __future__ import annotations
import logging
from typing import Callable, Optional, TYPE_CHECKING

try:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response
    STARLETTE_AVAILABLE = True
except ImportError:
    STARLETTE_AVAILABLE = False
    BaseHTTPMiddleware = object
    Request = None
    Response = None

from .context import RequestContext, set_context, clear_context

if TYPE_CHECKING:
    from .store import TraceStore

logger = logging.getLogger(__name__)


class TracingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that traces incoming HTTP requests.
    
    Creates a RequestContext at the start of each request, populates it
    during processing, and optionally saves it to a store at the end.
    
    Usage:
        from tracing import TracingMiddleware
        from tracing.store import SQLiteTraceStore
        
        store = SQLiteTraceStore("traces.db")
        app.add_middleware(TracingMiddleware, store=store)
    """
    
    def __init__(
        self,
        app,
        store: Optional['TraceStore'] = None,
        exclude_paths: Optional[set] = None,
        sample_rate: float = 1.0,
        save_threshold_ms: float = 0,  # Save all by default, or only slow ones
        save_errors: bool = True,
    ):
        """
        Initialize tracing middleware.
        
        Args:
            app: ASGI application
            store: Optional TraceStore to persist traces
            exclude_paths: Paths to exclude from tracing (e.g., /health)
            sample_rate: Fraction of requests to trace (0.0 to 1.0)
            save_threshold_ms: Only save traces slower than this (0 = save all)
            save_errors: Always save traces with errors regardless of threshold
        """
        if not STARLETTE_AVAILABLE:
            raise ImportError(
                "TracingMiddleware requires starlette. "
                "Install it with: pip install starlette"
            )
        super().__init__(app)
        self.store = store
        self.exclude_paths = exclude_paths or {"/health", "/metrics", "/favicon.ico"}
        self.sample_rate = sample_rate
        self.save_threshold_ms = save_threshold_ms
        self.save_errors = save_errors
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with tracing."""
        path = request.url.path
        
        # Skip excluded paths
        if path in self.exclude_paths:
            return await call_next(request)
        
        # Skip static files
        if path.startswith("/static"):
            return await call_next(request)
        
        # Sampling (for high-traffic scenarios)
        if self.sample_rate < 1.0:
            import random
            if random.random() > self.sample_rate:
                return await call_next(request)
        
        # Get or generate request ID
        request_id = (
            request.headers.get("X-Request-ID") or
            request.headers.get("X-Correlation-ID") or
            getattr(request.state, 'request_id', None)
        )
        
        # Create request context
        ctx = RequestContext.create(
            request_id=request_id,
            method=request.method,
            path=path,
        )
        
        # Set in context var
        set_context(ctx)
        
        # Process request
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as e:
            logger.exception(f"Request failed: {e}")
            raise
        finally:
            # End the context
            ctx.end()
            
            # Attach status code
            ctx.status_code = status_code
            
            # Try to extract user info from request state
            try:
                if hasattr(request.state, "user"):
                    user = request.state.user
                    ctx.user_id = str(getattr(user, "id", None) or getattr(user, "user_id", ""))
                    ctx.workspace_id = getattr(user, "workspace_id", None)
            except Exception:
                pass
            
            # Save trace if configured (fire-and-forget - non-blocking!)
            if self.store:
                should_save = (
                    (ctx.duration_ms and ctx.duration_ms >= self.save_threshold_ms) or
                    (self.save_errors and ctx.has_errors) or
                    (self.save_errors and status_code >= 400)
                )
                
                if should_save:
                    # Fire-and-forget: don't await, let it run in background
                    import asyncio
                    asyncio.create_task(self._save_trace_safe(ctx))
            
            # Clear context
            clear_context()
    
    async def _save_trace_safe(self, ctx: RequestContext) -> None:
        """Save trace to store (with error handling for background task)."""
        try:
            await self._save_trace(ctx)
        except Exception as e:
            logger.warning(f"Failed to save trace {ctx.request_id}: {e}")
    
    async def _save_trace(self, ctx: RequestContext) -> None:
        """Save trace to store."""
        if hasattr(self.store, 'save_async'):
            await self.store.save_async(ctx)
        elif hasattr(self.store, 'save'):
            import asyncio
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.store.save, ctx)
