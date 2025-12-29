"""
app_kernel.bootstrap - Simplified service creation.

Create a production-ready service in minutes with zero boilerplate.

Usage:
    from app_kernel.bootstrap import create_service, ServiceConfig
    
    # Minimal example
    app = create_service(
        name="order_service",
        routers=[orders_router, products_router],
    )
    
    # Full example with all options
    app = create_service(
        name="agent_service",
        version="1.0.0",
        description="AI Agents as a Service",
        
        # Your business logic
        routers=[
            agents_router,
            threads_router,
            chat_router,
        ],
        
        # Background tasks (optional)
        tasks={
            "process_document": process_document_handler,
            "send_notification": send_notification_handler,
        },
        
        # Configuration
        config=ServiceConfig(
            jwt_secret=os.environ["JWT_SECRET"],
            redis_url=os.environ.get("REDIS_URL"),
            database_url=os.environ.get("DATABASE_URL"),
            cors_origins=["http://localhost:3000"],
        ),
        
        # Lifecycle hooks (optional)
        on_startup=init_database,
        on_shutdown=close_connections,
        
        # Health checks (optional)
        health_checks=[check_db, check_redis],
        
        # Auth adapter (optional - for login/register routes)
        auth_service=get_auth_service,
    )

What you get for free:
- Auth (JWT tokens, login/register routes)
- CORS (configured or sensible defaults)
- Security headers
- Request ID tracking
- Structured logging
- Metrics endpoint (/metrics)
- Health endpoints (/healthz, /readyz)
- Rate limiting (if Redis configured)
- Idempotency (if Redis configured)
- Background jobs (if Redis configured)
- Error handling
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from fastapi import APIRouter, FastAPI

from .app import init_app_kernel
from .jobs import JobRegistry
from .settings import (
    AuthSettings,
    CorsSettings,
    FeatureSettings,
    JobSettings,
    KernelSettings,
    ObservabilitySettings,
    RedisSettings,
    ReliabilitySettings,
    SecuritySettings,
    StreamingSettings,
)


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class ServiceConfig:
    """
    Service configuration with sensible defaults.
    
    Only jwt_secret is truly required for production.
    Everything else has safe defaults.
    """
    # Auth (required for production)
    jwt_secret: str = "dev-secret-change-me"
    jwt_expiry_hours: int = 24
    auth_enabled: bool = True
    allow_self_signup: bool = False
    
    # Redis (optional - enables jobs, rate limiting, idempotency)
    redis_url: Optional[str] = None
    redis_key_prefix: str = "app:"
    
    # Database (optional - for health checks)
    database_url: Optional[str] = None
    
    # CORS
    cors_origins: List[str] = field(default_factory=lambda: ["*"])
    cors_credentials: bool = True
    
    # Rate limiting (requires Redis)
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window: int = 60
    
    # Streaming
    max_concurrent_streams: int = 3
    stream_lease_ttl: int = 300
    
    # Jobs (requires Redis)
    worker_count: int = 4
    job_max_attempts: int = 3
    
    # Debug
    debug: bool = False
    log_level: str = "INFO"
    
    @classmethod
    def from_env(cls, prefix: str = "") -> "ServiceConfig":
        """
        Load config from environment variables.
        
        Args:
            prefix: Optional prefix for env vars (e.g., "MY_APP_")
        
        Environment variables:
            {prefix}JWT_SECRET: Required for production
            {prefix}REDIS_URL: Enables jobs, rate limiting
            {prefix}DATABASE_URL: For health checks
            {prefix}CORS_ORIGINS: Comma-separated origins
            {prefix}DEBUG: Enable debug mode
        """
        def env(key: str, default: Any = None) -> Any:
            return os.environ.get(f"{prefix}{key}", default)
        
        def env_bool(key: str, default: bool = False) -> bool:
            val = env(key, str(default)).lower()
            return val in ("true", "1", "yes")
        
        def env_int(key: str, default: int) -> int:
            return int(env(key, default))
        
        def env_list(key: str, default: List[str]) -> List[str]:
            val = env(key)
            if val:
                return [s.strip() for s in val.split(",")]
            return default
        
        return cls(
            jwt_secret=env("JWT_SECRET", "dev-secret-change-me"),
            jwt_expiry_hours=env_int("JWT_EXPIRY_HOURS", 24),
            auth_enabled=env_bool("AUTH_ENABLED", True),
            allow_self_signup=env_bool("ALLOW_SELF_SIGNUP", False),
            redis_url=env("REDIS_URL"),
            redis_key_prefix=env("REDIS_KEY_PREFIX", "app:"),
            database_url=env("DATABASE_URL"),
            cors_origins=env_list("CORS_ORIGINS", ["*"]),
            cors_credentials=env_bool("CORS_CREDENTIALS", True),
            rate_limit_enabled=env_bool("RATE_LIMIT_ENABLED", True),
            rate_limit_requests=env_int("RATE_LIMIT_REQUESTS", 100),
            rate_limit_window=env_int("RATE_LIMIT_WINDOW", 60),
            max_concurrent_streams=env_int("MAX_CONCURRENT_STREAMS", 3),
            stream_lease_ttl=env_int("STREAM_LEASE_TTL", 300),
            worker_count=env_int("WORKER_COUNT", 4),
            job_max_attempts=env_int("JOB_MAX_ATTEMPTS", 3),
            debug=env_bool("DEBUG", False),
            log_level=env("LOG_LEVEL", "INFO"),
        )


# Type aliases
TaskHandler = Callable[[Dict[str, Any], Any], Awaitable[Any]]
HealthCheck = Callable[[], Awaitable[Tuple[bool, str]]]
LifecycleHook = Callable[[], Awaitable[None]]
RouterDef = Union[APIRouter, Tuple[str, APIRouter], Tuple[str, APIRouter, List[str]]]


# =============================================================================
# Main Entry Point
# =============================================================================

def create_service(
    name: str,
    *,
    # Core
    routers: Sequence[RouterDef] = (),
    tasks: Optional[Dict[str, TaskHandler]] = None,
    
    # Config
    config: Optional[ServiceConfig] = None,
    
    # Metadata
    version: str = "1.0.0",
    description: str = "",
    
    # Lifecycle
    on_startup: Optional[LifecycleHook] = None,
    on_shutdown: Optional[LifecycleHook] = None,
    
    # Health & Auth
    health_checks: Sequence[HealthCheck] = (),
    auth_service: Optional[Callable] = None,
    is_admin: Optional[Callable] = None,
    
    # Advanced
    api_prefix: str = "/api/v1",
    docs_url: str = "/docs",
    redoc_url: str = "/redoc",
) -> FastAPI:
    """
    Create a production-ready FastAPI service.
    
    Args:
        name: Service name (used in logs, metrics)
        routers: List of APIRouters to mount. Can be:
            - APIRouter (mounted at api_prefix)
            - (prefix, APIRouter) tuple
            - (prefix, APIRouter, tags) tuple
        tasks: Dict of task_name -> handler for background jobs
        config: ServiceConfig (or uses defaults/env vars)
        version: Service version
        description: API description
        on_startup: Async function called on startup
        on_shutdown: Async function called on shutdown
        health_checks: List of (name, check_fn) for /readyz
        auth_service: Factory function for auth service (enables login/register)
        is_admin: Function(user) -> bool for admin checks
        api_prefix: Prefix for app routers (default: /api/v1)
        docs_url: OpenAPI docs URL
        redoc_url: ReDoc URL
    
    Returns:
        Configured FastAPI application
    
    Example:
        app = create_service(
            name="widget_service",
            routers=[widgets_router, orders_router],
            tasks={"process_widget": process_widget},
            config=ServiceConfig.from_env("WIDGET_"),
        )
    """
    # Use provided config or load from env
    cfg = config or ServiceConfig.from_env()
    
    # Build job registry if tasks provided
    registry = None
    if tasks:
        registry = JobRegistry()
        for task_name, handler in tasks.items():
            registry.register(task_name, handler)
    
    # Build kernel settings from service config
    kernel_settings = _build_kernel_settings(name, version, cfg, health_checks)
    
    # Create lifespan
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .observability import get_logger, get_metrics
        logger = get_logger()
        metrics = get_metrics()
        
        # Run startup hook
        if on_startup:
            await on_startup()
        
        logger.info(f"{name} starting", extra={
            "version": version,
            "debug": cfg.debug,
            "redis": bool(cfg.redis_url),
        })
        metrics.set_gauge("service_started", 1)
        
        yield
        
        # Run shutdown hook
        if on_shutdown:
            await on_shutdown()
        
        logger.info(f"{name} shutting down")
    
    # Create FastAPI app
    app = FastAPI(
        title=name,
        description=description or f"{name} API",
        version=version,
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
    )
    
    # Get user store adapter if auth_service provided
    user_store = None
    if auth_service is not None:
        from .auth import AuthServiceAdapter
        user_store = AuthServiceAdapter(auth_service())
    
    # Initialize kernel
    init_app_kernel(
        app,
        kernel_settings,
        job_registry=registry,
        user_store=user_store,
        is_admin=is_admin or _default_is_admin,
        setup_reliability_middleware=bool(cfg.redis_url),
        mount_routers=True,
    )
    
    # Mount job routes if tasks defined and Redis available
    if tasks and cfg.redis_url:
        from .jobs import create_jobs_router, get_job_client
        
        # Note: Apps need to provide their own get_db for job status queries
        # For now, mount without DB dependency (basic status only)
        jobs_router = create_jobs_router(
            get_db=None,
            get_job_client=get_job_client,
            prefix="/jobs",
            tags=["jobs"],
        )
        app.include_router(jobs_router, prefix=api_prefix)
    
    # Mount app routers
    for router_def in routers:
        if isinstance(router_def, APIRouter):
            # Plain router - mount at api_prefix
            app.include_router(router_def, prefix=api_prefix)
        elif isinstance(router_def, tuple):
            if len(router_def) == 2:
                prefix, router = router_def
                app.include_router(router, prefix=f"{api_prefix}{prefix}")
            else:
                prefix, router, tags = router_def
                app.include_router(router, prefix=f"{api_prefix}{prefix}", tags=tags)
    
    # Root endpoint
    @app.get("/api")
    async def api_root():
        return {
            "service": name,
            "version": version,
            "docs": docs_url,
            "health": "/healthz",
            "ready": "/readyz",
        }
    
    return app


def _build_kernel_settings(
    name: str,
    version: str,
    cfg: ServiceConfig,
    health_checks: Sequence[HealthCheck],
) -> KernelSettings:
    """Build KernelSettings from ServiceConfig."""
    return KernelSettings(
        redis=RedisSettings(
            url=cfg.redis_url or "",
            key_prefix=cfg.redis_key_prefix,
        ) if cfg.redis_url else RedisSettings(),
        
        auth=AuthSettings(
            token_secret=cfg.jwt_secret,
            access_token_expires_minutes=cfg.jwt_expiry_hours * 60,
            enabled=cfg.auth_enabled,
        ),
        
        jobs=JobSettings(
            worker_count=cfg.worker_count,
            thread_pool_size=cfg.worker_count * 2,
            max_attempts=cfg.job_max_attempts,
        ),
        
        streaming=StreamingSettings(
            max_concurrent_per_user=cfg.max_concurrent_streams,
            lease_ttl_seconds=cfg.stream_lease_ttl,
        ),
        
        observability=ObservabilitySettings(
            service_name=name,
            log_level="DEBUG" if cfg.debug else cfg.log_level,
        ),
        
        reliability=ReliabilitySettings(
            rate_limit_requests=cfg.rate_limit_requests,
            rate_limit_window_seconds=cfg.rate_limit_window,
            rate_limit_enabled=cfg.rate_limit_enabled,
        ),
        
        cors=CorsSettings(
            enabled=True,
            allow_origins=tuple(cfg.cors_origins),
            allow_credentials=cfg.cors_credentials,
            allow_methods=("*",),
            allow_headers=("*",),
        ),
        
        security=SecuritySettings(
            enable_request_id=True,
            enable_security_headers=True,
            enable_request_logging=True,
            enable_error_handling=True,
            debug=cfg.debug,
        ),
        
        features=FeatureSettings(
            enable_health_routes=True,
            health_path="/healthz",
            ready_path="/readyz",
            enable_metrics=True,
            metrics_path="/metrics",
            protect_metrics="admin",
            enable_auth_routes=True,
            auth_mode="local",
            allow_self_signup=cfg.allow_self_signup,
            auth_prefix="/api/v1/auth",
            enable_audit_routes=False,
        ),
        
        health_checks=tuple(health_checks),
    )


def _default_is_admin(user) -> bool:
    """Default admin check."""
    if user is None:
        return False
    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    return role == "admin"


# =============================================================================
# Convenience: Quick Service (even simpler)
# =============================================================================

def quick_service(
    name: str,
    routers: Sequence[APIRouter],
    **kwargs,
) -> FastAPI:
    """
    Create a service with absolute minimum config.
    
    Uses environment variables for all configuration.
    
    Example:
        # In my_service.py
        from app_kernel.bootstrap import quick_service
        from .routes import widgets_router, orders_router
        
        app = quick_service("my_service", [widgets_router, orders_router])
    
    Environment variables:
        JWT_SECRET: Required for production
        REDIS_URL: Optional (enables jobs, rate limiting)
        DEBUG: Optional (enables debug mode)
    """
    return create_service(
        name=name,
        routers=routers,
        config=ServiceConfig.from_env(),
        **kwargs,
    )


__all__ = [
    "create_service",
    "quick_service",
    "ServiceConfig",
]
