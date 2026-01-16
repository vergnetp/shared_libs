"""
Security and observability middleware for app_kernel.

Provides:
- RequestIdMiddleware: Add unique request ID to each request
- SecurityHeadersMiddleware: Add security headers to responses
- RequestLoggingMiddleware: Log all requests with timing
- ErrorHandlingMiddleware: Global error handling
- CacheBustedStaticFiles: Static file serving with smart cache headers

All middleware is auto-configured by init_app_kernel() based on settings.
"""
import uuid
import time
import logging
from pathlib import Path
from typing import Callable, Tuple, Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from .settings import CorsSettings, SecuritySettings, TracingSettings


logger = logging.getLogger(__name__)


# =============================================================================
# Tracing Store (lazy loaded, accessed by admin routes)
# =============================================================================

_trace_store = None
_service_name = None


def get_trace_store():
    """Get the global trace store instance (for admin routes)."""
    return _trace_store


def get_traced_service_name():
    """Get the service name being traced."""
    return _service_name


def setup_tracing_middleware(
    app: FastAPI, 
    settings: TracingSettings,
    service_name: str = "unknown",
    db_path: Optional[str] = None,
) -> None:
    """
    Configure tracing middleware for request profiling.
    
    When enabled, traces every request with timing spans for:
    - The request itself
    - All outgoing HTTP calls (via http_client)
    - Any custom spans added with @traced decorator
    
    Args:
        app: FastAPI application
        settings: TracingSettings configuration
        service_name: Name of this service (for multi-service filtering)
        db_path: Path to trace database. If None, uses data/{service_name}_traces.db
    """
    global _trace_store, _service_name
    
    if not settings.enabled:
        logger.debug("Tracing: disabled")
        return
    
    _service_name = service_name
    
    try:
        from ..tracing import TracingMiddleware
        from ..tracing.store import SQLiteTraceStore
        
        # Use shared traces.db - services on same server will share traces
        # This allows the admin dashboard to view all services' traces
        if db_path is None:
            db_path = "data/traces.db"
        
        # Ensure data directory exists
        import os
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        
        # Create store with service name
        _trace_store = SQLiteTraceStore(db_path, service_name=service_name)
        
        # Add middleware
        app.add_middleware(
            TracingMiddleware,
            store=_trace_store,
            exclude_paths=set(settings.exclude_paths),
            sample_rate=settings.sample_rate,
            save_threshold_ms=settings.save_threshold_ms,
            save_errors=settings.save_errors,
        )
        
        logger.info(f"Tracing: enabled for '{service_name}', db={db_path}, sample_rate={settings.sample_rate}")
        
    except ImportError as e:
        logger.warning(f"Tracing: could not import tracing module: {e}")
    except Exception as e:
        logger.error(f"Tracing: failed to initialize: {e}")


# =============================================================================
# Cache-Busted Static Files
# =============================================================================

class CacheBustedStaticFiles(StaticFiles):
    """
    StaticFiles with smart cache control headers.
    
    - HTML files: no-cache (always revalidate)
    - Hashed assets (main.abc123.js): immutable, 1 year cache
    - Non-hashed assets: 1 hour cache with revalidate
    
    Usage:
        from app_kernel.middleware import CacheBustedStaticFiles
        
        app.mount("/", CacheBustedStaticFiles(directory="static", html=True), name="static")
    
    This replaces FastAPI's default StaticFiles to ensure HTML pages
    are never cached by browsers or CDNs (like Cloudflare).
    """
    
    # Extensions that are static assets
    STATIC_EXTENSIONS = {'.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot', '.webp', '.avif', '.mp4', '.webm', '.map'}
    
    # Characters that make up a hash
    HASH_CHARS = set('0123456789abcdef')
    
    def _has_hash_in_filename(self, path: str) -> bool:
        """Check if filename contains a hash (Vite/webpack style)."""
        if not path:
            return False
        
        filename = Path(path).name
        name_parts = filename.rsplit('.', 1)
        if len(name_parts) < 2:
            return False
        
        name = name_parts[0]
        
        # Check for hash separated by . or -
        for sep in ('.', '-'):
            if sep in name:
                potential_hash = name.rsplit(sep, 1)[-1]
                if len(potential_hash) >= 8 and all(c in self.HASH_CHARS for c in potential_hash.lower()):
                    return True
        
        return False
    
    def _get_cache_headers(self, path: str) -> dict:
        """Determine cache headers based on file path."""
        suffix = Path(path).suffix.lower()
        
        if suffix == '.html' or not suffix:
            # HTML - never cache
            return {
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0',
                'CDN-Cache-Control': 'no-store',
                'Cloudflare-CDN-Cache-Control': 'no-store',
            }
        elif suffix in self.STATIC_EXTENSIONS:
            if self._has_hash_in_filename(path):
                # Hashed asset - cache forever
                return {'Cache-Control': 'public, max-age=31536000, immutable'}
            else:
                # Non-hashed asset - cache 1 hour
                return {'Cache-Control': 'public, max-age=3600, must-revalidate'}
        else:
            # Unknown - short cache
            return {'Cache-Control': 'public, max-age=300, must-revalidate'}
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Override to inject cache headers into responses."""
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return
        
        path = scope.get("path", "")
        cache_headers = self._get_cache_headers(path)
        
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                
                # Add our cache headers
                for key, value in cache_headers.items():
                    headers.append((key.lower().encode(), value.encode()))
                
                message = {**message, "headers": headers}
            
            await send(message)
        
        await super().__call__(scope, receive, send_with_headers)


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
    - Cache-Control: smart caching based on content type
    
    Cache strategy:
    - Static assets with hash in filename (Vite/Svelte): long cache (1 year)
    - Static assets without hash (.js, .css, etc): short cache (1 hour)
    - HTML files: no-cache (always revalidate)
    - API responses: no-store
    """
    
    # Extensions that are static assets
    STATIC_EXTENSIONS = {'.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot', '.webp', '.avif', '.mp4', '.webm', '.map'}
    
    # Pattern for hashed filenames (Vite/webpack style: name.abc123.js or name-abc123.js)
    # Matches: main.a1b2c3d4.js, style-5f6g7h8i.css, etc.
    HASH_PATTERN_CHARS = set('0123456789abcdef')
    
    def _has_hash_in_filename(self, path: str) -> bool:
        """Check if filename contains a hash (for cache busting)."""
        # Get filename without extension
        parts = path.split('/')
        if not parts:
            return False
        filename = parts[-1]
        
        # Look for patterns like .abc123. or -abc123.
        # Vite uses: name-[hash].ext or name.[hash].ext
        name_parts = filename.rsplit('.', 1)
        if len(name_parts) < 2:
            return False
        
        name = name_parts[0]
        
        # Check for hash separated by . or -
        for sep in ('.', '-'):
            if sep in name:
                potential_hash = name.rsplit(sep, 1)[-1]
                # Hash is typically 8+ hex characters
                if len(potential_hash) >= 8 and all(c in self.HASH_PATTERN_CHARS for c in potential_hash.lower()):
                    return True
        
        return False
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        path = request.url.path
        
        # Determine cache strategy based on path/content
        if path.startswith("/api/"):
            # API responses - never cache
            response.headers["Cache-Control"] = "no-store, max-age=0"
        
        elif any(path.endswith(ext) for ext in self.STATIC_EXTENSIONS):
            # Static assets
            if self._has_hash_in_filename(path):
                # Hashed filename (Vite/webpack) - cache for 1 year (immutable)
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            else:
                # Non-hashed static file - cache for 1 hour, revalidate
                response.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
        
        elif path.endswith('.html') or path == '/' or '.' not in path.split('/')[-1]:
            # HTML files and routes (no extension = likely SPA route) - always revalidate
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            # Tell Cloudflare not to cache this
            response.headers["CDN-Cache-Control"] = "no-store"
            response.headers["Cloudflare-CDN-Cache-Control"] = "no-store"
        
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
