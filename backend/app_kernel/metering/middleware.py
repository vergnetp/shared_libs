"""Usage metering middleware - pushes events to Redis."""

import time
from typing import Callable, Optional, Set
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class UsageMeteringMiddleware(BaseHTTPMiddleware):
    """
    Middleware that tracks API usage via Redis.
    
    Usage:
        app.add_middleware(
            UsageMeteringMiddleware,
            redis_client=redis,
            app_name="deploy_api",
            exclude_paths={"/healthz", "/metrics"},
        )
    """
    
    def __init__(
        self,
        app,
        redis_client,
        app_name: str,
        exclude_paths: Optional[Set[str]] = None,
        get_user_from_request: Optional[Callable] = None,
    ):
        super().__init__(app)
        self.redis = redis_client
        self.app_name = app_name
        self.exclude_paths = exclude_paths or {
            "/healthz", "/readyz", "/metrics", "/favicon.ico", "/docs", "/openapi.json"
        }
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
        
        # Push to Redis (fire and forget)
        try:
            from .publisher import track_request
            
            await track_request(
                self.redis,
                app=self.app_name,
                user_id=user_id,
                workspace_id=workspace_id,
                endpoint=path,
                method=request.method,
                status_code=response.status_code,
                latency_ms=latency_ms,
                bytes_in=bytes_in,
                bytes_out=bytes_out,
            )
        except Exception:
            pass  # Never fail the request
        
        return response
