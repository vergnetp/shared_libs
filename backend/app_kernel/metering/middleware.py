"""Usage metering middleware - auto-tracks all requests."""

import time
from typing import Callable, Optional, Set
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class UsageMeteringMiddleware(BaseHTTPMiddleware):
    """
    Middleware that automatically tracks API usage.
    
    Usage:
        app.add_middleware(
            UsageMeteringMiddleware,
            get_db_connection=get_db_connection,
            exclude_paths={"/healthz", "/metrics"},
            log_individual=False,  # Set True for detailed logs
        )
    """
    
    def __init__(
        self,
        app,
        get_db_connection: Callable,
        exclude_paths: Optional[Set[str]] = None,
        log_individual: bool = False,
        get_user_from_request: Optional[Callable] = None,
    ):
        super().__init__(app)
        self.get_db_connection = get_db_connection
        self.exclude_paths = exclude_paths or {
            "/healthz", "/readyz", "/metrics", "/favicon.ico", "/docs", "/openapi.json"
        }
        self.log_individual = log_individual
        self.get_user_from_request = get_user_from_request
    
    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip excluded paths
        path = request.url.path
        if path in self.exclude_paths or any(path.startswith(p) for p in self.exclude_paths if p.endswith("/")):
            return await call_next(request)
        
        # Track timing
        start = time.time()
        
        # Get request size
        bytes_in = 0
        if request.headers.get("content-length"):
            try:
                bytes_in = int(request.headers.get("content-length"))
            except:
                pass
        
        # Process request
        response = await call_next(request)
        
        # Calculate metrics
        latency_ms = int((time.time() - start) * 1000)
        
        # Get response size
        bytes_out = 0
        if response.headers.get("content-length"):
            try:
                bytes_out = int(response.headers.get("content-length"))
            except:
                pass
        
        # Extract user/workspace info
        user_id = None
        workspace_id = None
        
        # Try to get from request state (set by auth middleware)
        if hasattr(request.state, "user"):
            user = request.state.user
            user_id = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
            workspace_id = getattr(user, "workspace_id", None) or (user.get("workspace_id") if isinstance(user, dict) else None)
        
        # Or use custom extractor
        if self.get_user_from_request and not user_id:
            try:
                user_info = await self.get_user_from_request(request)
                if user_info:
                    user_id = user_info.get("user_id")
                    workspace_id = user_info.get("workspace_id")
            except:
                pass
        
        # Track asynchronously (don't block response)
        try:
            from .stores import track_request
            
            async with self.get_db_connection() as db:
                await track_request(
                    db,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    endpoint=path,
                    method=request.method,
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                    bytes_in=bytes_in,
                    bytes_out=bytes_out,
                    log_individual=self.log_individual,
                )
        except Exception as e:
            # Don't fail the request if metering fails
            import logging
            logging.warning(f"Usage metering failed: {e}")
        
        return response
