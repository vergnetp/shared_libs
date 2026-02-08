"""
app_kernel.bootstrap - Service creation internals.

For most users, use create_service from app_kernel directly:

    from app_kernel import create_service
    
    app = create_service(
        name="my-api",
        database_url="postgresql://...",
        redis_url="redis://...",
        jwt_secret="your-32-char-secret...",
        cors_origins=["https://myapp.com"],
        routers=[my_router],
    )

See app_kernel.create_service for full documentation.

This module contains:
- ServiceConfig: Internal configuration dataclass
- Internal create_service: Used by the public create_service wrapper
"""

from __future__ import annotations

import asyncio
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
    TracingSettings,
)


# =============================================================================
# Configuration
# =============================================================================

def _parse_database_url(url: str) -> dict:
    """
    Parse database URL into components.
    
    Formats:
        sqlite:///./data/app.db           (relative)
        sqlite:////absolute/path/app.db   (Unix absolute)
        sqlite:///C:/Users/.../app.db     (Windows absolute)
        postgres://user:pass@host:5432/dbname
        mysql://user:pass@host:3306/dbname
    
    Returns:
        {"type": "sqlite"|"postgres"|"mysql", "name": str, "host": str, "port": int, "user": str, "password": str}
    """
    from urllib.parse import urlparse, unquote
    import re
    
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    
    if scheme == "sqlite":
        # Extract path after sqlite:///
        # Handle: sqlite:///./rel, sqlite:////abs, sqlite:///C:/win
        path = url.split("sqlite:///", 1)[1] if "sqlite:///" in url else parsed.path
        
        # Windows drive letter detection (C:/, D:/, etc.)
        if re.match(r'^[A-Za-z]:/', path):
            pass  # Already correct: C:/Users/...
        elif path.startswith("/") and re.match(r'^/[A-Za-z]:/', path):
            path = path[1:]  # Remove leading slash: /C:/... -> C:/...
        # Unix absolute path
        elif path.startswith("/"):
            pass  # Keep as is: /home/user/...
        # Relative path
        # ./data/app.db stays as is
        
        return {
            "type": "sqlite",
            "name": path,
            "host": None,
            "port": None,
            "user": None,
            "password": None,
        }
    elif scheme in ("postgres", "postgresql"):
        return {
            "type": "postgres",
            "name": parsed.path.lstrip("/"),
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "user": unquote(parsed.username) if parsed.username else None,
            "password": unquote(parsed.password) if parsed.password else None,
        }
    elif scheme == "mysql":
        return {
            "type": "mysql",
            "name": parsed.path.lstrip("/"),
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 3306,
            "user": unquote(parsed.username) if parsed.username else None,
            "password": unquote(parsed.password) if parsed.password else None,
        }
    else:
        raise ValueError(f"Unsupported database scheme: {scheme}")


async def _run_embedded_admin_worker(
    redis_url: str,
    admin_db_url: str,
    app_name: str,
    logger,
    batch_size: int = 100,
    poll_interval: float = 0.5,
):
    """
    Run admin worker as background task (consumes audit/metering from Redis).
    
    With multiple uvicorn workers, Redis RPOP is atomic so events get
    distributed across workers automatically - each event processed exactly once.
    """
    import json
    import uuid
    from datetime import datetime, timezone
    
    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning("redis library not installed, admin worker disabled")
        return
    
    redis_client = aioredis.from_url(redis_url)
    
    # Use databases library for admin_db (same as admin_worker.py)
    try:
        from databases import Database
        admin_db = Database(admin_db_url)
        await admin_db.connect()
    except ImportError:
        logger.warning("databases library not installed, admin worker disabled")
        return
    except Exception as e:
        logger.warning(f"Could not connect to admin_db: {e}")
        return
    
    # Initialize schemas
    try:
        from .audit.schema import init_audit_schema
        from .metering.schema import init_metering_schema
        await init_audit_schema(admin_db)
        await init_metering_schema(admin_db)
    except Exception as e:
        logger.warning(f"Could not init admin schemas: {e}")
    
    def _now_iso():
        return datetime.now(timezone.utc).isoformat()
    
    async def process_audit_event(event: dict):
        await admin_db.execute(
            """INSERT INTO audit_logs (id, app, entity, entity_id, action, changes, 
               old_snapshot, new_snapshot, user_id, request_id, timestamp, created_at)
               VALUES (:id, :app, :entity, :entity_id, :action, :changes,
               :old_snapshot, :new_snapshot, :user_id, :request_id, :timestamp, :created_at)""",
            {
                "id": str(uuid.uuid4()),
                "app": event.get("app"),
                "entity": event.get("entity"),
                "entity_id": event.get("entity_id"),
                "action": event.get("action"),
                "changes": json.dumps(event.get("changes")) if event.get("changes") else None,
                "old_snapshot": json.dumps(event.get("old_snapshot")) if event.get("old_snapshot") else None,
                "new_snapshot": json.dumps(event.get("new_snapshot")) if event.get("new_snapshot") else None,
                "user_id": event.get("user_id"),
                "request_id": event.get("request_id"),
                "timestamp": event.get("timestamp", _now_iso()),
                "created_at": _now_iso(),
            }
        )
    
    async def process_metering_event(event: dict):
        # Simplified: just insert raw events, aggregation done at query time
        await admin_db.execute(
            """INSERT INTO usage_events (id, app, workspace_id, user_id, event_type,
               endpoint, method, status_code, latency_ms, bytes_in, bytes_out, 
               period, timestamp)
               VALUES (:id, :app, :workspace_id, :user_id, :event_type,
               :endpoint, :method, :status_code, :latency_ms, :bytes_in, :bytes_out,
               :period, :timestamp)""",
            {
                "id": str(uuid.uuid4()),
                "app": event.get("app"),
                "workspace_id": event.get("workspace_id"),
                "user_id": event.get("user_id"),
                "event_type": event.get("type", "request"),
                "endpoint": event.get("endpoint"),
                "method": event.get("method"),
                "status_code": event.get("status_code"),
                "latency_ms": event.get("latency_ms"),
                "bytes_in": event.get("bytes_in"),
                "bytes_out": event.get("bytes_out"),
                "period": event.get("period"),
                "timestamp": event.get("timestamp", _now_iso()),
            }
        )
    
    try:
        while True:
            processed = 0
            
            # Process audit events
            for _ in range(batch_size):
                event_data = await redis_client.rpop("admin:audit_events")
                if not event_data:
                    break
                try:
                    event = json.loads(event_data)
                    await process_audit_event(event)
                    processed += 1
                except Exception as e:
                    logger.debug(f"Audit event error: {e}")
            
            # Process metering events
            for _ in range(batch_size):
                event_data = await redis_client.rpop("admin:metering_events")
                if not event_data:
                    break
                try:
                    event = json.loads(event_data)
                    await process_metering_event(event)
                    processed += 1
                except Exception as e:
                    logger.debug(f"Metering event error: {e}")
            
            # Sleep if no events
            if processed == 0:
                await asyncio.sleep(poll_interval)
    
    except asyncio.CancelledError:
        pass
    finally:
        await admin_db.disconnect()
        await redis_client.close()


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
    allow_self_signup: bool = True  # Allow open registration
    
    # SaaS (workspaces, members, invites)
    saas_enabled: bool = True
    saas_invite_base_url: Optional[str] = None  # e.g., "https://app.example.com/invite"
    
    # OAuth (auto-mounts routes when providers configured)
    oauth_providers: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Example: {"google": {"client_id": "...", "client_secret": "..."}, "github": {...}}
    
    # Redis (optional - enables jobs, rate limiting, idempotency)
    redis_url: Optional[str] = None
    redis_key_prefix: str = "queue:"  # Match job_queue default
    
    # Database URL (kernel manages connection pool, app provides schema)
    # Format: sqlite:///./data/app.db or postgres://user:pass@host:5432/dbname
    database_url: Optional[str] = None
    
    # Admin worker (consumes audit/metering from Redis, writes to DB)
    # When True: runs as background task in one of the uvicorn workers
    # When False: you run `python -m app_kernel.admin_worker` separately
    admin_worker_embedded: bool = True
    admin_db_url: Optional[str] = None  # Separate DB for admin worker (defaults to database_url)
    
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
    
    # Email (optional - enables invite emails, notifications)
    email_enabled: bool = False
    email_provider: str = "smtp"  # smtp, ses, sendgrid
    email_from: Optional[str] = None
    email_reply_to: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    
    # Debug
    debug: bool = False
    log_level: str = "INFO"
    
    # Request Metrics (captures latency, errors, geo per request - requires Redis for async storage)
    request_metrics_enabled: bool = False
    request_metrics_exclude_paths: List[str] = field(default_factory=lambda: [
        "/health", "/healthz", "/readyz", "/metrics", "/favicon.ico"
    ])
    
    # Tracing (for admin telemetry dashboard) - enabled by default
    tracing_enabled: bool = True
    tracing_exclude_paths: List[str] = field(default_factory=lambda: [
        "/health", "/healthz", "/readyz", "/metrics", "/favicon.ico"
    ])
    tracing_sample_rate: float = 1.0


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
    
    # Config (required)
    config: Optional[ServiceConfig] = None,
    
    # Database schema init (async function that takes db connection)
    schema_init: Optional[Callable] = None,
    
    # Metadata
    version: str = "1.0.0",
    description: str = "",
    
    # Lifecycle
    on_startup: Optional[LifecycleHook] = None,
    on_shutdown: Optional[LifecycleHook] = None,
    
    # Health & Auth
    health_checks: Sequence[HealthCheck] = (),
    user_store: Optional[Any] = None,
    is_admin: Optional[Callable[[Any], bool]] = None,  # Custom admin check function
    
    # Advanced
    api_prefix: str = "/api/v1",
    docs_url: str = "/docs",
    redoc_url: str = "/redoc",
    
    # Functional testing (admin only, opt-in)
    test_runners: Optional[List[Callable]] = None,
) -> FastAPI:
    """
    Internal: Create a FastAPI service from ServiceConfig.
    
    Use app_kernel.create_service() for the public API.
    """
    if config is None:
        raise ValueError("config is required")
    
    cfg = config
    
    # Collect integration tasks and routers
    integration_tasks = {}
    integration_routers = []
    
    # Setup request metrics task if enabled (requires Redis)
    request_metrics_enabled = cfg.request_metrics_enabled and cfg.redis_url
    if request_metrics_enabled:
        from .observability.request_metrics import store_request_metrics
        integration_tasks["store_request_metrics"] = store_request_metrics
    
    # Build job registry - merge app tasks with integration tasks
    all_tasks = {**(tasks or {}), **integration_tasks}
    registry = None
    if all_tasks:
        registry = JobRegistry()
        for task_name, handler in all_tasks.items():
            registry.register(task_name, handler)
    
    # Build kernel settings from service config
    kernel_settings = _build_kernel_settings(name, version, cfg, health_checks, test_runners=test_runners)
    
    # Create lifespan
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .observability import get_logger, get_metrics
        logger = get_logger()
        metrics = get_metrics()
        
        # Run environment validation checks (fails in prod if misconfigured)
        from .env_checks import run_env_checks, get_env
        try:
            run_env_checks(kernel_settings)
            logger.info(f"Environment: {get_env().upper()}")
        except RuntimeError as e:
            logger.error(str(e))
            raise
        
        # Track resolved Redis URL (may differ from config if fallback used)
        resolved_redis_url = cfg.redis_url
        
        # Auto-start Redis/Postgres via Docker if localhost and not running
        # Redis always succeeds (has in-memory fallback)
        try:
            from .dev_deps import ensure_dev_deps, is_fake_redis_url
            deps_result = await ensure_dev_deps(
                database_url=cfg.database_url,
                redis_url=cfg.redis_url,
            )
            # Use resolved URL (might be fakeredis://, localhost, or original)
            if "redis" in deps_result:
                resolved_redis_url = deps_result["redis"].get("url", cfg.redis_url)
        except Exception as e:
            logger.debug(f"Dev deps: {e}")
        
        # Initialize database if configured
        if cfg.database_url:
            from .db import init_db_session, init_schema, get_db_connection
            
            # Parse database URL
            db_config = _parse_database_url(cfg.database_url)
            
            # Ensure data directory exists for SQLite
            if db_config["type"] == "sqlite":
                from pathlib import Path
                Path(db_config["name"]).parent.mkdir(parents=True, exist_ok=True)
                init_db_session(
                    database_name=db_config["name"],
                    database_type="sqlite",
                )
            else:
                init_db_session(
                    database_name=db_config["name"],
                    database_type=db_config["type"],
                    host=db_config["host"],
                    port=db_config["port"] or 5432,
                    user=db_config["user"],
                    password=db_config["password"],
                )
            logger.info(f"Database initialized", extra={
                "type": db_config["type"],
                "database": db_config["name"],
            })
            
            # Run automated backup and migration (schema-first)
            try:
                from .db.lifecycle import run_database_lifecycle, get_lifecycle_config
                from .db import get_db_connection
                
                lifecycle_config = get_lifecycle_config()
                
                async with get_db_connection() as db:
                    lifecycle_result = await run_database_lifecycle(
                        db,
                        data_dir=lifecycle_config["data_dir"],
                        backup_enabled=lifecycle_config["backup_enabled"],
                        migration_enabled=lifecycle_config["migration_enabled"],
                    )
                
                if lifecycle_result["backup_created"]:
                    logger.info("✓ Automated backup completed")
                
                if lifecycle_result["migration_applied"]:
                    logger.info("✓ Schema migration completed", extra={
                        "migration_id": lifecycle_result["migration_id"]
                    })
            except Exception as e:
                logger.error(f"Database lifecycle failed: {e}")
                # Fail startup if lifecycle fails (especially migrations)
                raise
            
            # Auto-enable audit logging if Redis is configured (and not fakeredis)
            from .dev_deps import is_fake_redis_url
            if resolved_redis_url and not is_fake_redis_url(resolved_redis_url):
                from .db.session import enable_auto_audit
                enable_auto_audit(resolved_redis_url, name)
                logger.info("Audit logging enabled (Redis → admin_worker)")
            
            # Initialize ALL kernel schemas at once
            from .db.schema import init_all_schemas
            await init_schema(lambda db: init_all_schemas(
                db, 
                saas_enabled=cfg.saas_enabled,
                request_metrics_enabled=request_metrics_enabled
            ))
            logger.info("Kernel schemas initialized")
            
            # Initialize app schema if provided
            if schema_init:
                await init_schema(schema_init)
                logger.info("App database schema initialized")
        
        # Setup email integration (if enabled)
        if cfg.email_enabled:
            from .integrations.email import setup_kernel_email
            if setup_kernel_email(cfg):
                logger.info(f"Email configured: {cfg.smtp_host}:{cfg.smtp_port}")
            else:
                logger.warning("Email enabled but setup failed - check SMTP settings")
        
        # Run app startup hook
        if on_startup:
            await on_startup()
        
        logger.info(f"{name} starting", extra={
            "version": version,
            "debug": cfg.debug,
            "redis": bool(cfg.redis_url),
            "database": bool(cfg.database_url),
        })
        metrics.set_gauge("service_started", 1)
        
        # Start embedded admin worker if enabled
        admin_worker_task = None
        if cfg.redis_url and cfg.admin_worker_embedded:
            admin_db_url = cfg.admin_db_url or cfg.database_url
            if admin_db_url:
                admin_worker_task = asyncio.create_task(
                    _run_embedded_admin_worker(cfg.redis_url, admin_db_url, name, logger)
                )
                logger.info("Embedded admin worker started")
        
        yield
        
        # Stop embedded admin worker
        if admin_worker_task:
            admin_worker_task.cancel()
            try:
                await admin_worker_task
            except asyncio.CancelledError:
                pass
            logger.info("Embedded admin worker stopped")
        
        # Run app shutdown hook
        if on_shutdown:
            await on_shutdown()
        
        # Close HTTP connection pools (cloud clients, etc.)
        try:
            from shared_libs.backend.http_client import close_pool
            await close_pool()
            logger.debug("HTTP connection pools closed")
        except ImportError:
            pass  # http_client not available
        except Exception as e:
            logger.warning(f"Error closing HTTP pools: {e}")
        
        # Close database
        if cfg.database_url:
            from .db import close_db
            await close_db()
            logger.info("Database closed")
        
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
    
    # Get user store - either direct or auto-create from DB
    _user_store = user_store  # Direct user_store takes precedence
    
    # Auto-create user store if DB + auth enabled and no store provided
    if _user_store is None and cfg.database_url and cfg.auth_enabled:
        from .db import get_db_connection
        from .auth.stores import create_kernel_user_store
        _user_store = create_kernel_user_store(get_db_connection)
    
    # Initialize kernel
    init_app_kernel(
        app,
        kernel_settings,
        job_registry=registry,
        user_store=_user_store,
        is_admin=is_admin or _default_is_admin,
        setup_reliability_middleware=bool(cfg.redis_url),
        mount_routers=True,
        test_runners=test_runners,
        api_prefix=api_prefix,
    )
    
    # Add request metrics middleware if enabled
    if request_metrics_enabled:
        from .observability.request_metrics import RequestMetricsMiddleware
        from .jobs import get_job_client
        
        # Middleware is added AFTER kernel init so job_client is available
        app.add_middleware(
            RequestMetricsMiddleware,
            job_client=get_job_client(),
            exclude_paths=set(cfg.request_metrics_exclude_paths),
        )
    
    # Add usage metering middleware when Redis is configured (auto-enabled)
    if cfg.redis_url:
        from .metering.middleware import UsageMeteringMiddleware
        import redis.asyncio as aioredis
        
        try:
            metering_redis = aioredis.from_url(cfg.redis_url)
            app.add_middleware(
                UsageMeteringMiddleware,
                redis_client=metering_redis,
                app_name=name,
            )
            # Note: metering data is NOT purged - it's usage data for billing
        except Exception as e:
            # Don't fail app startup if metering can't be set up
            from .observability import get_logger
            get_logger().warning(f"Could not enable usage metering: {e}")
    
    # Mount request metrics API routes if enabled
    if request_metrics_enabled:
        from .observability.request_metrics import create_request_metrics_router
        from .auth.deps import get_current_user
        
        metrics_router = create_request_metrics_router(
            prefix="/metrics/requests",
            protect="admin",
            get_current_user=get_current_user,
            is_admin=is_admin or _default_is_admin,
        )
        app.include_router(metrics_router, prefix=api_prefix)
    
    # Mount audit log routes (admin only)
    if cfg.database_url:
        from .audit import create_audit_router
        from .auth.deps import get_current_user
        from .db import db_dependency
        
        audit_router = create_audit_router(
            get_current_user=get_current_user,
            db_dependency=db_dependency,
            app_name=name,
            prefix="/audit",
            require_admin=True,
            is_admin=is_admin or _default_is_admin,
        )
        app.include_router(audit_router, prefix=api_prefix)
    
    # Mount usage metering routes
    if cfg.database_url:
        from .metering import create_metering_router
        from .auth.deps import get_current_user
        from .db import db_dependency
        
        metering_router = create_metering_router(
            get_current_user=get_current_user,
            db_dependency=db_dependency,
            app_name=name,
            prefix="/usage",
            is_admin=is_admin or _default_is_admin,
        )
        app.include_router(metering_router, prefix=api_prefix)
    
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
    
    # Mount task cancellation routes (always available)
    from .tasks import create_tasks_router
    _tasks_auth = None
    if cfg.auth_enabled:
        from .auth.deps import get_current_user as _tasks_auth
    tasks_router = create_tasks_router(auth_dependency=_tasks_auth)
    app.include_router(tasks_router, prefix=api_prefix)
    
    # Mount OAuth routes if providers configured
    if cfg.oauth_providers:
        from .oauth import create_oauth_router, configure_providers
        from .auth.deps import get_current_user, get_current_user_optional
        from .auth.utils import create_access_token
        from .auth.stores import create_kernel_user_store
        from .db import get_db_connection
        
        # Configure providers
        configure_providers(cfg.oauth_providers)
        
        # Create user function for OAuth signup
        async def oauth_create_user(email: str, name: str = None):
            if _user_store:
                return await _user_store.create_user(email=email, name=name, password=None)
            return None
        
        # Create JWT function
        def oauth_create_jwt(user):
            return create_access_token(
                user_id=user.get("id") or user.id,
                email=user.get("email") or user.email,
                role=user.get("role", "user"),
                secret=cfg.jwt_secret,
                expires_minutes=cfg.jwt_expiry_hours * 60,
            )
        
        oauth_router = create_oauth_router(
            get_db_connection=get_db_connection,
            get_current_user=get_current_user,
            get_current_user_optional=get_current_user_optional,
            create_user=oauth_create_user,
            create_jwt_token=oauth_create_jwt,
            prefix="/auth/oauth",
            allow_signup=True,  # Always allow registration via OAuth
        )
        app.include_router(oauth_router, prefix=api_prefix)
        logger.info(f"OAuth enabled: {', '.join(cfg.oauth_providers.keys())}")
    
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
    
    # Mount integration routers (billing, etc.)
    for router in integration_routers:
        app.include_router(router, prefix=api_prefix)
    
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
    test_runners: Optional[List[Callable]] = None,
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
            request_metrics_enabled=cfg.request_metrics_enabled,
            request_metrics_exclude_paths=tuple(cfg.request_metrics_exclude_paths),
        ),
        
        tracing=TracingSettings(
            enabled=cfg.tracing_enabled,
            exclude_paths=tuple(cfg.tracing_exclude_paths),
            sample_rate=cfg.tracing_sample_rate,
        ),
        
        reliability=ReliabilitySettings(
            rate_limit_enabled=cfg.rate_limit_enabled,
            rate_limit_anonymous_rpm=cfg.rate_limit_requests,  # Use as base rate
            rate_limit_authenticated_rpm=cfg.rate_limit_requests * 4,  # 4x for authenticated
            rate_limit_admin_rpm=cfg.rate_limit_requests * 20,  # 20x for admin
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
            enable_saas_routes=cfg.saas_enabled,
            saas_invite_base_url=cfg.saas_invite_base_url,
            enable_test_routes=bool(test_runners),
        ),
        
        health_checks=tuple(health_checks)
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