"""
Security and observability middleware for app_kernel.

Provides:
- RequestIdMiddleware: Add unique request ID to each request
- SecurityHeadersMiddleware: Add security headers to responses
- RequestLoggingMiddleware: Log all requests with timing
- ErrorHandlingMiddleware: Global error handling

All middleware is auto-configured by init_app_kernel() based on settings.
"""
import uuid
import time
import logging
from typing import Callable, Tuple

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .settings import CorsSettings, SecuritySettings


logger = logging.getLogger(__name__)


# =============================================================================
# Request ID Middleware
# =============================================================================

class RequestIdMiddleware(BaseHTTPMiddleware):
    """
    Add unique request ID to each request.
    
    - Adds X-Request-ID header to response
    - Stores request_id in request.state for access in routes
    - Uses incoming X-Request-ID if present (for distributed tracing)
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Use incoming request ID or generate new one
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        
        # Store in request state for access in routes
        request.state.request_id = request_id
        
        # Call next middleware/route
        response = await call_next(request)
        
        # Add to response headers
        response.headers["X-Request-ID"] = request_id
        
        return response


# =============================================================================
# Security Headers Middleware
# =============================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all responses.
    
    Headers added:
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - X-XSS-Protection: 1; mode=block
    - Referrer-Policy: strict-origin-when-cross-origin
    - Cache-Control: no-store (for API responses)
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Prevent caching of API responses (exclude static files)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        
        return response


# =============================================================================
# Request Logging Middleware
# =============================================================================

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log all requests with timing and context.
    
    Logs:
    - Request method, path, status
    - Duration in ms
    - Request ID
    - User ID (if authenticated)
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.perf_counter()
        
        # Get request context
        request_id = getattr(request.state, "request_id", "unknown")
        method = request.method
        path = request.url.path
        
        # Call next middleware/route
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000
        
        # Get user ID if available (set by auth middleware)
        user_id = getattr(request.state, "user_id", None)
        
        # Log based on status code
        status_code = response.status_code
        log_data = {
            "request_id": request_id,
            "method": method,
            "path": path,
            "status": status_code,
            "duration_ms": round(duration_ms, 2),
        }
        
        if user_id:
            log_data["user_id"] = user_id
        
        if status_code >= 500:
            logger.error(f"Request failed: {log_data}")
        elif status_code >= 400:
            logger.warning(f"Request error: {log_data}")
        else:
            # Only log at debug for health checks to reduce noise
            if path in ("/healthz", "/readyz", "/health"):
                logger.debug(f"Request: {log_data}")
            else:
                logger.info(f"Request: {log_data}")
        
        return response


# =============================================================================
# Error Handling Middleware
# =============================================================================

class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """
    Global error handling to prevent leaking internal details.
    
    In production:
    - Catches unhandled exceptions
    - Returns generic 500 error
    - Logs full exception details
    
    In debug mode:
    - Re-raises exceptions for FastAPI's default handler
    """
    
    def __init__(self, app, debug: bool = False):
        super().__init__(app)
        self.debug = debug
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            return await call_next(request)
        except Exception as e:
            request_id = getattr(request.state, "request_id", "unknown")
            
            # Log full exception
            logger.exception(
                f"Unhandled exception: request_id={request_id}, "
                f"path={request.url.path}, error={type(e).__name__}: {e}"
            )
            
            if self.debug:
                # Re-raise for FastAPI's debug error page
                raise
            
            # Return generic error in production
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "request_id": request_id,
                },
            )


# =============================================================================
# Setup Functions
# =============================================================================

def setup_cors(app: FastAPI, settings: CorsSettings) -> None:
    """Configure CORS middleware."""
    if not settings.enabled:
        return
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allow_origins),
        allow_credentials=settings.allow_credentials,
        allow_methods=list(settings.allow_methods),
        allow_headers=list(settings.allow_headers),
    )
    
    logger.debug(f"CORS enabled: origins={settings.allow_origins}")


def setup_security_middleware(app: FastAPI, settings: SecuritySettings) -> None:
    """
    Configure all security middleware.
    
    Order matters! Middleware executes in reverse order of addition.
    """
    # Add in reverse order of execution
    
    # 1. Error handling (outermost - catches all)
    if settings.enable_error_handling:
        app.add_middleware(ErrorHandlingMiddleware, debug=settings.debug)
    
    # 2. Request logging (after error handling)
    if settings.enable_request_logging:
        app.add_middleware(RequestLoggingMiddleware)
    
    # 3. Security headers
    if settings.enable_security_headers:
        app.add_middleware(SecurityHeadersMiddleware)
    
    # 4. Request ID (innermost - runs first)
    if settings.enable_request_id:
        app.add_middleware(RequestIdMiddleware)
    
    logger.debug(
        f"Security middleware configured: "
        f"request_id={settings.enable_request_id}, "
        f"headers={settings.enable_security_headers}, "
        f"logging={settings.enable_request_logging}, "
        f"error_handling={settings.enable_error_handling}"
    )
