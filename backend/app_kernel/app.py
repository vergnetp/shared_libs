"""
app_kernel.app - Main initialization entrypoint.

Provides init_app_kernel() which wires everything together.

IMPORTANT: Workers are separate processes, not part of FastAPI lifecycle.
The kernel provides worker code; deployment decides how to run workers.

Usage:
    from fastapi import FastAPI
    from app_kernel import init_app_kernel, KernelSettings
    from app_kernel.jobs import JobRegistry
    
    app = FastAPI()
    
    # Create job registry
    registry = JobRegistry()
    
    @registry.task("process_document")
    async def process_document(payload, ctx):
        ...
    
    # Initialize kernel (for API process)
    settings = KernelSettings(
        auth=AuthSettings(token_secret=os.environ["JWT_SECRET"]),
        redis=RedisSettings(url=os.environ["REDIS_URL"]),
    )
    
    init_app_kernel(app, settings, registry)
    
    # Access kernel components via app.state.kernel
    # logger = app.state.kernel.logger
    # metrics = app.state.kernel.metrics
    
    # Workers run separately - see jobs/worker.py for worker process example
"""
from dataclasses import dataclass
from typing import Optional, Callable, Any

from fastapi import FastAPI, Request

from .settings import KernelSettings
from .jobs import JobRegistry

# Check if job_queue is available
try:
    from ..job_queue.config import QueueRedisConfig
    JOB_QUEUE_AVAILABLE = True
except ImportError:
    JOB_QUEUE_AVAILABLE = False
    QueueRedisConfig = None


@dataclass
class KernelRuntime:
    """
    Runtime state for an initialized kernel.
    
    Stored on app.state.kernel after init_app_kernel() completes.
    Access via app.state.kernel.logger, app.state.kernel.metrics, etc.
    """
    logger: Any  # KernelLogger or logging.Logger
    metrics: Any  # MetricsCollector
    audit: Any  # AuditLogger
    settings: KernelSettings
    
    # Optional components (may be None if not configured)
    redis_config: Any = None  # QueueRedisConfig if Redis available
    job_registry: Optional[JobRegistry] = None


def get_kernel(app: FastAPI) -> KernelRuntime:
    """
    Get the kernel runtime from a FastAPI app.
    
    Usage:
        kernel = get_kernel(app)
        kernel.logger.info("Hello")
        kernel.metrics.increment("requests")
    
    Raises:
        RuntimeError: If kernel not initialized
    """
    if not hasattr(app.state, "kernel"):
        raise RuntimeError("Kernel not initialized. Call init_app_kernel() first.")
    return app.state.kernel


def init_app_kernel(
    app: FastAPI,
    settings: KernelSettings,
    job_registry: Optional[JobRegistry] = None,
    user_loader: Optional[Callable] = None,
    user_store = None,  # For auth router (UserStore protocol)
    is_admin: Optional[Callable] = None,  # For metrics protection
    setup_reliability_middleware: bool = True,
    mount_routers: bool = True,  # Auto-mount kernel routers
) -> None:
    """
    Initialize the app kernel. SIDE-EFFECTFUL.
    
    This function fully initializes the app with all kernel infrastructure:
    - CORS middleware (if enabled)
    - Security middleware (request ID, headers, logging, error handling)
    - Observability (logging, metrics, audit)
    - Job client (for enqueueing)
    - Rate limiting and idempotency (if Redis available)
    - Auto-mounted routers (health, metrics, auth)
    
    After calling this, access components via app.state.kernel:
        app.state.kernel.logger
        app.state.kernel.metrics
        app.state.kernel.audit
    
    NOTE: This does NOT start workers. Workers run as separate processes.
    
    Args:
        app: FastAPI application
        settings: KernelSettings configuration (frozen, immutable)
        job_registry: Optional job registry for task dispatch
        user_loader: Optional async function to load user by ID (for auth deps)
        user_store: Optional UserStore for auth router (login/register)
        is_admin: Optional function(user) -> bool for admin checks
        setup_reliability_middleware: Whether to add rate limiting/idempotency
        mount_routers: Whether to auto-mount kernel routers based on settings.features
    
    Returns:
        None - access components via app.state.kernel
    """
    # =========================================================================
    # 1. Initialize observability first (so other components can log)
    # =========================================================================
    from .observability.logging import init_kernel_logger
    from .observability.metrics import init_metrics
    from .observability.audit import init_audit
    
    # Try to use log module, fall back to basic config
    try:
        from ..log.config import LoggerConfig, LogLevel
        
        logger_config = LoggerConfig(
            service_name=settings.observability.service_name,
            min_level=LogLevel.from_string(settings.observability.log_level),
            log_dir=settings.observability.log_dir,
            add_caller_info=settings.observability.add_caller_info,
            quiet_init=True,
        )
        logger = init_kernel_logger(logger_config)
    except ImportError:
        # Fallback: use basic logging
        logger = init_kernel_logger(None)
    
    metrics = init_metrics()
    audit = init_audit()
    
    logger.info(f"Initializing app_kernel for {settings.observability.service_name}")
    
    # =========================================================================
    # 2. Setup CORS middleware (must be early)
    # =========================================================================
    from .middleware import setup_cors, setup_security_middleware, setup_tracing_middleware
    
    setup_cors(app, settings.cors)
    if settings.cors.enabled:
        logger.info(f"CORS: enabled, origins={settings.cors.allow_origins}")
    
    # =========================================================================
    # 3. Setup security middleware (request ID, headers, logging, errors)
    # =========================================================================
    setup_security_middleware(app, settings.security)
    logger.info(
        f"Security middleware: request_id={settings.security.enable_request_id}, "
        f"headers={settings.security.enable_security_headers}, "
        f"logging={settings.security.enable_request_logging}, "
        f"debug={settings.security.debug}"
    )
    
    # =========================================================================
    # 3b. Setup tracing middleware (for admin telemetry dashboard)
    # =========================================================================
    setup_tracing_middleware(
        app, 
        settings.tracing,
        service_name=settings.observability.service_name,
    )
    
    # =========================================================================
    # 4. Initialize Redis-based components (if job_queue available)
    # =========================================================================
    redis_config = None
    
    if JOB_QUEUE_AVAILABLE and settings.redis.url:
        redis_config = QueueRedisConfig(
            url=settings.redis.url,
            key_prefix=settings.redis.key_prefix,
            max_connections=settings.redis.max_connections,
            socket_timeout=settings.redis.socket_timeout,
            socket_connect_timeout=settings.redis.socket_connect_timeout,
        )
    elif settings.redis.url:
        logger.warning("Redis URL configured but job_queue not installed - using in-memory fallbacks")
    
    # =========================================================================
    # 5. Initialize streaming lifecycle
    # =========================================================================
    from ..streaming.leases import init_lease_limiter, StreamLeaseConfig
    
    stream_config = StreamLeaseConfig(
        limit=settings.streaming.max_concurrent_per_user,
        ttl_seconds=settings.streaming.lease_ttl_seconds,
    )
    
    init_lease_limiter(redis_config, stream_config)
    logger.info(f"Streaming: max {settings.streaming.max_concurrent_per_user} concurrent per user")
    
    # =========================================================================
    # 6. Initialize job queue (if job_queue available)
    # =========================================================================
    if job_registry is not None and JOB_QUEUE_AVAILABLE and redis_config is not None:
        try:
            from ..job_queue.config import (
                QueueConfig,
                QueueWorkerConfig,
                QueueRetryConfig,
                QueueLoggingConfig,
            )
            from ..job_queue import QueueManager, QueueWorker
            from .jobs.client import init_job_client
            from .jobs.worker import init_worker_manager
            
            # Create queue config
            worker_config = QueueWorkerConfig(
                worker_count=settings.jobs.worker_count,
                thread_pool_size=settings.jobs.thread_pool_size,
                work_timeout=settings.jobs.work_timeout,
            )
            
            retry_config = QueueRetryConfig.exponential(
                max_attempts=settings.jobs.max_attempts,
                min_delay=5.0,
                max_delay=300.0,
            )
            
            logging_config = QueueLoggingConfig(
                logger=logger.logger if hasattr(logger, 'logger') else None,
            )
            
            queue_config = QueueConfig(
                redis=redis_config,
                worker=worker_config,
                retry=retry_config,
                logging=logging_config,
            )
            
            # Create manager and worker
            queue_manager = QueueManager(queue_config)
            queue_worker = QueueWorker(queue_config)
            
            # Initialize kernel's job client and worker manager
            init_job_client(queue_manager, job_registry)
            init_worker_manager(queue_worker, job_registry, queue_config)
            
            logger.info(f"Jobs: {len(job_registry)} tasks registered, {settings.jobs.worker_count} workers")
        except Exception as e:
            logger.warning(f"Failed to initialize job queue: {e}")
    elif job_registry is not None:
        logger.warning("Job registry provided but job_queue not available - jobs will not be processed")
    
    # =========================================================================
    # 7. Initialize auth
    # =========================================================================
    if settings.auth.enabled:
        from .auth.deps import init_auth_deps
        
        init_auth_deps(settings.auth.token_secret, user_loader)
        logger.info("Auth: enabled")
    
    # =========================================================================
    # 8. Initialize database session factory
    # =========================================================================
    if settings.database_url:
        logger.info(f"Database: URL configured (call init_db_session with connection manager)")
    
    # =========================================================================
    # 9. Setup reliability middleware (if Redis available)
    # =========================================================================
    if setup_reliability_middleware and settings.reliability.rate_limit_enabled and redis_config is not None:
        from .reliability.ratelimit import init_rate_limiter, RateLimitConfig
        
        rate_config = RateLimitConfig(
            requests=settings.reliability.rate_limit_requests,
            window_seconds=settings.reliability.rate_limit_window_seconds,
        )
        
        init_rate_limiter(redis_config, rate_config)
        logger.info(f"Rate limiting: {settings.reliability.rate_limit_requests} req/{settings.reliability.rate_limit_window_seconds}s")
    
    if setup_reliability_middleware and settings.reliability.idempotency_enabled and redis_config is not None:
        from .reliability.idempotency import init_idempotency_checker, IdempotencyConfig
        
        idempotency_config = IdempotencyConfig(
            ttl_seconds=settings.reliability.idempotency_ttl_seconds,
        )
        
        init_idempotency_checker(redis_config, idempotency_config)
        logger.info(f"Idempotency: TTL {settings.reliability.idempotency_ttl_seconds}s")
    
    # =========================================================================
    # 10. Auto-mount kernel routers based on feature settings
    # =========================================================================
    if mount_routers:
        _mount_kernel_routers(
            app=app,
            settings=settings,
            user_store=user_store,
            is_admin=is_admin,
            logger=logger,
        )
    
    # =========================================================================
    # 11. Store kernel runtime on app.state
    # =========================================================================
    kernel_runtime = KernelRuntime(
        logger=logger,
        metrics=metrics,
        audit=audit,
        settings=settings,
        redis_config=redis_config,
        job_registry=job_registry,
    )
    
    app.state.kernel = kernel_runtime
    app.state.kernel_settings = settings  # Legacy compat
    app.state.kernel_initialized = True
    
    logger.info(f"app_kernel initialized for {settings.observability.service_name}")


def _mount_kernel_routers(
    app: FastAPI,
    settings: KernelSettings,
    user_store,
    is_admin: Optional[Callable],
    logger,
):
    """
    Mount kernel routers based on feature settings.
    
    This keeps router mounting logic separate from main init flow.
    """
    features = settings.features
    prefix = features.kernel_prefix
    
    # -------------------------------------------------------------------------
    # Health routes (always safe, no auth)
    # -------------------------------------------------------------------------
    if features.enable_health_routes:
        from .health.router import create_health_router
        
        health_router = create_health_router(
            health_checks=settings.health_checks,
            health_path=features.health_path,
            ready_path=features.ready_path,
        )
        app.include_router(health_router, prefix=prefix)
        logger.info(f"Mounted health routes: {features.health_path}, {features.ready_path}")
    
    # -------------------------------------------------------------------------
    # Metrics route (protected)
    # -------------------------------------------------------------------------
    if features.enable_metrics:
        from .observability.metrics import create_metrics_router
        from .auth.deps import get_current_user
        
        metrics_router = create_metrics_router(
            metrics_path=features.metrics_path,
            protect_metrics=features.protect_metrics,
            get_current_user=get_current_user if features.protect_metrics == "admin" else None,
            is_admin=is_admin,
        )
        app.include_router(metrics_router, prefix=prefix)
        logger.info(f"Mounted metrics route: {features.metrics_path} (protection: {features.protect_metrics})")
    
    # -------------------------------------------------------------------------
    # Auth routes (if local auth mode and user_store provided)
    # -------------------------------------------------------------------------
    if features.enable_auth_routes and features.auth_mode == "local":
        if user_store is not None:
            from .auth.router import create_auth_router
            
            # If SaaS is enabled, create personal workspace on signup
            on_signup = None
            if features.enable_saas_routes:
                from .saas.deps import get_or_create_personal_workspace
                on_signup = get_or_create_personal_workspace
            
            auth_router = create_auth_router(
                user_store=user_store,
                token_secret=settings.auth.token_secret,
                access_token_expires_minutes=settings.auth.access_token_expires_minutes,
                refresh_token_expires_days=settings.auth.refresh_token_expires_days,
                prefix=features.auth_prefix,
                on_signup=on_signup,
            )
            app.include_router(auth_router, prefix=prefix)
            logger.info(f"Mounted auth routes: {features.auth_prefix}")
        else:
            logger.warning("Auth routes enabled but user_store not provided - skipping auth router")
    
    # -------------------------------------------------------------------------
    # Audit routes (admin only, optional)
    # -------------------------------------------------------------------------
    if features.enable_audit_routes:
        # TODO: Create audit router
        logger.info(f"Audit routes: enabled at {features.audit_path} (not yet implemented)")
    
    # -------------------------------------------------------------------------
    # Job routes (status, list, cancel)
    # -------------------------------------------------------------------------
    if features.enable_job_routes:
        from .jobs.router import create_jobs_router
        
        # get_db dependency needs to be provided by the app
        # For now, log that it's enabled but app must mount manually if they need DB queries
        logger.info(f"Job routes: enabled at {features.job_routes_prefix} (mount with create_jobs_router)")
        # NOTE: Apps should call create_jobs_router(get_db=their_get_db) and mount it
        # because kernel doesn't know the app's database dependency
    
    # -------------------------------------------------------------------------
    # SaaS routes (workspaces, members, invites)
    # -------------------------------------------------------------------------
    if features.enable_saas_routes:
        from .saas.router import create_saas_router
        
        saas_router = create_saas_router(
            invite_base_url=features.saas_invite_base_url,
        )
        app.include_router(saas_router, prefix=f"{prefix}/api/v1")
        logger.info(f"SaaS routes: enabled (workspaces, members, invites)")
