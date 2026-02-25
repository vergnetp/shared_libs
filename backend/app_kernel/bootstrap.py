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
    redis_client,
    app_name: str,
    logger,
    batch_size: int = 100,
    poll_interval: float = 5,
):
    """
    Run admin worker as background task.
    
    Drains three Redis queues and batch-saves to DB:
    - admin:audit_events     → kernel_audit_logs
    - admin:metering_events  → kernel_usage_events
    - admin:request_metrics  → kernel_request_metrics
    
    Uses the app's own DB connection pool (raw_db_context) and a shared
    Redis client (same instance as all publishers).
    """
    import json
    import uuid
    from datetime import datetime, timezone
    
    from .db.session import raw_db_context
    
    def _now_iso():
        return datetime.now(timezone.utc).isoformat()
    
    def _build_audit_entity(event: dict) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "entity": event.get("entity"),
            "entity_id": event.get("entity_id"),
            "action": event.get("action"),
            "changes": json.dumps(event.get("changes")) if event.get("changes") else None,
            "old_snapshot": json.dumps(event.get("old_snapshot")) if event.get("old_snapshot") else None,
            "new_snapshot": json.dumps(event.get("new_snapshot")) if event.get("new_snapshot") else None,
            "user_id": event.get("user_id"),
            "request_id": event.get("request_id"),
            "timestamp": event.get("timestamp", _now_iso()),
        }
    
    def _build_metering_entity(event: dict) -> dict:
        return {
            "id": str(uuid.uuid4()),
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
    
    def _build_request_metric_entity(event: dict) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "request_id": event.get("request_id"),
            "method": event.get("method"),
            "path": event.get("path"),
            "query_params": event.get("query_params"),
            "status_code": event.get("status_code"),
            "server_latency_ms": event.get("server_latency_ms"),
            "client_ip": event.get("client_ip"),
            "user_agent": event.get("user_agent"),
            "referer": event.get("referer"),
            "user_id": event.get("user_id"),
            "workspace_id": event.get("workspace_id"),
            "country": event.get("country"),
            "city": event.get("city"),
            "continent": event.get("continent"),
            "error": event.get("error"),
            "error_type": event.get("error_type"),
            "timestamp": event.get("timestamp", _now_iso()),
            "year": event.get("year"),
            "month": event.get("month"),
            "day": event.get("day"),
            "hour": event.get("hour"),
        }
    
    # Queue definitions: (redis_key, table_name, builder_func)
    queues = [
        ("admin:audit_events", "kernel_audit_logs", _build_audit_entity),
        ("admin:metering_events", "kernel_usage_events", _build_metering_entity),
        ("admin:request_metrics", "kernel_request_metrics", _build_request_metric_entity),
    ]
    
    try:
        logger.info("Admin worker: polling started")
        consecutive_redis_errors = 0
        max_backoff = 60  # Cap at 60 seconds
        
        while True:
            # Drain all queues
            all_batches = []  # [(table, entity_dict), ...]
            
            try:
                for redis_key, table, builder in queues:
                    for _ in range(batch_size):
                        data = await redis_client.rpop(redis_key)
                        if not data:
                            break
                        try:
                            all_batches.append((table, builder(json.loads(data))))
                        except Exception as e:
                            logger.debug(f"Event parse error ({redis_key}): {e}")
                consecutive_redis_errors = 0  # Reset on success
            except Exception as e:
                consecutive_redis_errors += 1
                backoff = min(poll_interval * (2 ** consecutive_redis_errors), max_backoff)
                logger.warning(f"Admin worker Redis error (attempt {consecutive_redis_errors}, retry in {backoff:.0f}s): {e}")
                
                # After repeated failures, try to refresh the client
                if consecutive_redis_errors >= 5:
                    try:
                        from .redis import get_redis
                        refreshed = get_redis()
                        if refreshed is not None:
                            redis_client = refreshed
                            logger.info("Admin worker: refreshed Redis client")
                    except Exception:
                        pass
                
                await asyncio.sleep(backoff)
                continue
            
            # Write batch in single connection
            if all_batches:
                # Count per type for logging
                counts = {}
                for table, _ in all_batches:
                    counts[table] = counts.get(table, 0) + 1
                summary = ", ".join(f"{v} {k.replace('kernel_', '')}" for k, v in counts.items())
                logger.info(f"Admin worker: processing {summary}")
                
                try:
                    async with raw_db_context() as db:
                        for table, entity in all_batches:
                            await db.save_entity(table, entity)
                except Exception as e:
                    logger.warning(f"Admin worker DB error: {e}")
                    # Push failed events to dead-letter queue for later retry
                    try:
                        for table, entity in all_batches:
                            dlq_entry = json.dumps({"table": table, "entity": entity})
                            await redis_client.lpush("admin:dead_letter", dlq_entry)
                        logger.info(f"Admin worker: {len(all_batches)} events moved to dead-letter queue")
                    except Exception as dlq_err:
                        logger.error(f"Admin worker: failed to write to dead-letter queue: {dlq_err}")
            else:
                await asyncio.sleep(poll_interval)
    
    except asyncio.CancelledError:
        pass


def _next_cron_run(cron_str: str) -> float:
    """
    Calculate seconds until the next cron match.
    
    Supports standard 5-field cron: minute hour day month weekday
    Each field supports: number, *, */N, comma-separated values, ranges.
    
    Uses field-level jumps instead of minute-by-minute iteration.
    Worst case: ~400 iterations (one per day for irregular month/weekday combos).
    """
    from datetime import datetime, timezone, timedelta
    
    fields = cron_str.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Invalid cron string: {cron_str!r} (need 5 fields: min hour day month weekday)")
    
    def _parse_field(field: str, min_val: int, max_val: int) -> set:
        values = set()
        for part in field.split(','):
            part = part.strip()
            if part == '*':
                values.update(range(min_val, max_val + 1))
            elif part.startswith('*/'):
                step = int(part[2:])
                values.update(range(min_val, max_val + 1, step))
            elif '-' in part:
                lo, hi = part.split('-', 1)
                values.update(range(int(lo), int(hi) + 1))
            else:
                values.add(int(part))
        return values
    
    minutes = sorted(_parse_field(fields[0], 0, 59))
    hours = sorted(_parse_field(fields[1], 0, 23))
    days = _parse_field(fields[2], 1, 31)
    months = _parse_field(fields[3], 1, 12)
    weekdays_cron = _parse_field(fields[4], 0, 6)
    
    # Convert cron weekday (0=Sun) to Python weekday (0=Mon)
    py_weekdays = set()
    for wd in weekdays_cron:
        py_weekdays.add((wd - 1) % 7)
    
    now = datetime.now(timezone.utc)
    candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    
    # Jump day-by-day, then find first valid hour:minute within each day
    for _ in range(400):  # Max ~13 months of days
        if candidate.month in months and candidate.day in days and candidate.weekday() in py_weekdays:
            # This day is valid — find earliest valid hour:minute at or after candidate time
            for h in hours:
                if h < candidate.hour:
                    continue
                for m in minutes:
                    if h == candidate.hour and m < candidate.minute:
                        continue
                    result = candidate.replace(hour=h, minute=m)
                    return (result - now).total_seconds()
        
        # Jump to start of next day
        candidate = (candidate + timedelta(days=1)).replace(hour=0, minute=0)
    
    raise ValueError(f"No matching time found for cron: {cron_str!r}")


async def _run_backup_loop(backup_dir: str, cron_str: str, logger):
    """
    Cron-scheduled database backup as a background task.
    
    Args:
        backup_dir: Directory for backup files
        cron_str: Cron schedule string (5-field)
        logger: Logger instance
    """
    from .db.session import raw_db_context
    from pathlib import Path
    
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    
    while True:
        try:
            wait = _next_cron_run(cron_str)
            logger.info(f"Backup worker: next run in {wait/3600:.1f}h ({cron_str})")
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            raise
        
        try:
            async with raw_db_context() as db:
                tables = await db.list_tables()
                user_tables = [t for t in tables if not t.startswith('_')]
                
                if not user_tables:
                    logger.debug("Backup skipped — no user tables")
                else:
                    from ..databases.backup import BackupStrategy
                    strategy = BackupStrategy(db)
                    result = await strategy.backup_database(
                        backup_dir,
                        include_native=True,
                        include_csv=True,
                    )
                    logger.info(f"Periodic backup complete: {len(user_tables)} tables", extra={
                        "csv_dir": result.get("csv_dir"),
                        "tables": len(user_tables),
                    })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Backup error: {e}")


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
    
    @classmethod
    def from_env(cls) -> "ServiceConfig":
        """
        Create ServiceConfig from environment variables.
        
        Environment variables:
            JWT_SECRET: JWT signing secret (required for production)
            DATABASE_URL: Database connection string
            REDIS_URL: Redis connection string
            CORS_ORIGINS: Comma-separated list of allowed origins
            DEBUG: Enable debug mode (true/false/1/0)
            LOG_LEVEL: Logging level (DEBUG, INFO, WARNING, ERROR)
            RATE_LIMIT_RPM: Requests per minute (default: 100)
            SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD: Email settings
            EMAIL_FROM: Sender email address
        """
        import os
        
        def _bool(val: str) -> bool:
            return val.lower() in ("true", "1", "yes")
        
        cors_raw = os.getenv("CORS_ORIGINS", "*")
        cors_origins = [o.strip() for o in cors_raw.split(",") if o.strip()]
        
        debug = _bool(os.getenv("DEBUG", "false"))
        
        kwargs = dict(
            jwt_secret=os.getenv("JWT_SECRET", "dev-secret-change-me"),
            database_url=os.getenv("DATABASE_URL"),
            redis_url=os.getenv("REDIS_URL"),
            cors_origins=cors_origins,
            debug=debug,
            log_level=os.getenv("LOG_LEVEL", "DEBUG" if debug else "INFO"),
        )
        
        # Optional overrides
        if os.getenv("RATE_LIMIT_RPM"):
            kwargs["rate_limit_requests"] = int(os.getenv("RATE_LIMIT_RPM"))
        
        # Email
        smtp_host = os.getenv("SMTP_HOST")
        if smtp_host:
            kwargs.update(
                email_enabled=True,
                smtp_host=smtp_host,
                smtp_port=int(os.getenv("SMTP_PORT", "587")),
                smtp_user=os.getenv("SMTP_USER"),
                smtp_password=os.getenv("SMTP_PASSWORD"),
                email_from=os.getenv("EMAIL_FROM"),
            )
        
        return cls(**kwargs)


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
    
    # Background backup (cron string, None to disable, default daily 3pm UTC)
    backup_schedule: Optional[str] = "0 15 * * *",
    
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
    
    # In dev mode, resolve database_url early so database-dependent routes get mounted.
    # ensure_database() does the same during lifespan, but routers are registered before that.
    if not cfg.database_url:
        from .env_checks import is_prod
        if not is_prod():
            from pathlib import Path
            data_dir = Path("./data")
            data_dir.mkdir(parents=True, exist_ok=True)
            cfg.database_url = f"sqlite:///{data_dir / f'{name}.db'}"
    
    # Collect integration tasks and routers
    integration_tasks = {}
    integration_routers = []
    
    # Request metrics are handled by admin worker (no job_queue needed)
    request_metrics_enabled = cfg.request_metrics_enabled
    
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
        resolved_database_url = cfg.database_url
        
        # Auto-start Redis/Postgres via Docker if localhost and not running
        # Redis always succeeds (has in-memory fallback)
        # Database auto-provisions in non-prod (SQLite fallback if Docker unavailable)
        try:
            from .dev_deps import ensure_dev_deps
            deps_result = await ensure_dev_deps(
                database_url=cfg.database_url,
                redis_url=cfg.redis_url,
                service_name=name,
            )
            # Use resolved URL (might be fakeredis://, localhost, or original)
            if "redis" in deps_result:
                resolved_redis_url = deps_result["redis"].get("url", cfg.redis_url)
            # Use resolved database URL (might be auto-created SQLite, Docker Postgres, etc.)
            if "database" in deps_result and deps_result["database"].get("url"):
                resolved_database_url = deps_result["database"]["url"]
        except RuntimeError:
            # Prod with no DATABASE_URL — re-raise
            raise
        except Exception as e:
            logger.debug(f"Dev deps: {e}")
        
        # Shared Redis client for audit + admin worker
        shared_redis_client = None
        
        # Initialize database if configured
        if resolved_database_url:
            from .db.session import init_db_session, init_schema, get_db_connection
            
            # Parse database URL
            db_config = _parse_database_url(resolved_database_url)
            
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
            
            # Register auto-connection provider for entity methods
            # This lets entity classmethods (get, find, save, etc.) work without
            # an explicit db parameter — each call auto-acquires from the pool.
            from .db.session import db_context
            from shared_libs.backend.databases import set_connection_provider
            set_connection_provider(db_context)
            
            # Run automated backup and migration (schema-first)
            try:
                from .db.lifecycle import run_database_lifecycle, get_lifecycle_config
                from .db.session import get_db_connection
                
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
            
            # Initialize shared Redis client (one pool for the whole app)
            if resolved_redis_url:
                from .redis import init_redis, get_redis, is_fake as _is_redis_fake
                init_redis(resolved_redis_url)
                shared_redis_client = get_redis()
                is_fake = _is_redis_fake()
                
                from .db.session import enable_auto_audit
                enable_auto_audit(shared_redis_client, name)
                logger.info("Audit logging enabled (Redis → admin_worker)")
            else:
                shared_redis_client = None
                is_fake = True
            
            # Initialize cache (Redis if real, in-memory if dev, disabled if prod without Redis)
            from .cache import init_cache
            from .env_checks import get_env
            is_prod = get_env().lower() in ("prod", "production")
            cache = init_cache(
                redis_client=shared_redis_client,
                is_fake=is_fake,
                is_prod=is_prod,
            )
            logger.info(f"Cache: {cache.backend_type}")
            
            # Initialize ALL kernel schemas at once
            from .schema import init_all_schemas
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
        if cfg.admin_worker_embedded and shared_redis_client:
            admin_worker_task = asyncio.create_task(
                _run_embedded_admin_worker(shared_redis_client, name, logger)
            )
            logger.info("Embedded admin worker started")
        
        # Start periodic backup worker
        backup_task = None
        if backup_schedule and resolved_database_url:
            from .db.lifecycle import get_lifecycle_config
            _backup_dir = get_lifecycle_config().get("data_dir", ".data") + "/backups"
            backup_task = asyncio.create_task(
                _run_backup_loop(_backup_dir, backup_schedule, logger)
            )
            logger.info(f"Periodic backup worker started ({backup_schedule})")
        
        yield
        
        # Stop backup worker
        if backup_task:
            backup_task.cancel()
            try:
                await backup_task
            except asyncio.CancelledError:
                pass
            logger.info("Backup worker stopped")
        
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
            from .db.session import close_db
            await close_db()
            logger.info("Database closed")
        
        # Teardown tracing
        try:
            from .observability.tracing import teardown_tracing
            teardown_tracing()
        except Exception:
            pass
        
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
        from .db.session import get_db_connection
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
    
    # Add request metrics middleware if enabled (uses shared Redis → admin worker)
    # NOTE: Redis is initialized during lifespan, not at module-init time.
    # Pass a lazy getter so the middleware resolves the client on first request.
    if request_metrics_enabled and cfg.redis_url:
        from .observability.request_metrics import RequestMetricsMiddleware
        from .redis import get_redis as _get_metrics_redis
        
        app.add_middleware(
            RequestMetricsMiddleware,
            redis_client_factory=_get_metrics_redis,
            exclude_paths=set(cfg.request_metrics_exclude_paths),
        )
    
    # Add usage metering middleware (uses shared Redis → admin worker)
    if cfg.redis_url:
        from .metering.middleware import UsageMeteringMiddleware
        from .redis import get_redis as _get_metering_redis
        
        app.add_middleware(
            UsageMeteringMiddleware,
            redis_client_factory=_get_metering_redis,
            app_name=name,
        )
    
    # Mount request metrics API routes if enabled (admin only)
    if request_metrics_enabled:
        from .observability.request_metrics import create_request_metrics_router
        from .auth.deps import get_current_user
        
        metrics_router = create_request_metrics_router(
            prefix="/admin/metrics",
            protect="admin",
            get_current_user=get_current_user,
            is_admin=is_admin or _default_is_admin,
        )
        app.include_router(metrics_router, prefix=api_prefix)
    
    # Mount audit log routes (admin only)
    if cfg.database_url:
        from .audit import create_audit_router
        from .auth.deps import get_current_user
        
        audit_router = create_audit_router(
            get_current_user=get_current_user,
            app_name=name,
            prefix="/admin/audit",
            require_admin=True,
            is_admin=is_admin or _default_is_admin,
        )
        app.include_router(audit_router, prefix=api_prefix)
    
    # Mount action replay routes (frontend error diagnosis)
    if cfg.database_url:
        from .action_replay import create_action_replay_router
        from .auth.deps import get_current_user, get_current_user_optional
        
        replay_router = create_action_replay_router(
            get_current_user=get_current_user,
            get_current_user_optional=get_current_user_optional,
            prefix="/admin",
            is_admin=is_admin or _default_is_admin,
        )
        app.include_router(replay_router, prefix=api_prefix)
    
    # Mount database admin routes (admin only)
    if cfg.database_url:
        from .db.lifecycle import get_lifecycle_config
        from .db.router import create_db_admin_router
        from .auth.deps import get_current_user
        
        lifecycle_cfg = get_lifecycle_config()
        db_admin_router = create_db_admin_router(
            get_current_user=get_current_user,
            data_dir=lifecycle_cfg.get("data_dir", ".data"),
            prefix="/admin/db",
            is_admin=is_admin or _default_is_admin,
        )
        app.include_router(db_admin_router, prefix=api_prefix)
    
    # Mount usage metering routes (user-facing + admin)
    if cfg.database_url:
        from .metering import create_metering_router, create_metering_admin_router
        from .auth.deps import get_current_user
        
        # User-facing: own usage, own workspace, quota checks
        metering_router = create_metering_router(
            get_current_user=get_current_user,
            app_name=name,
            prefix="/usage",
            is_admin=is_admin or _default_is_admin,
        )
        app.include_router(metering_router, prefix=api_prefix)
        
        # Admin: cross-user and cross-workspace queries
        metering_admin_router = create_metering_admin_router(
            get_current_user=get_current_user,
            app_name=name,
            prefix="/admin/usage",
            is_admin=is_admin or _default_is_admin,
        )
        app.include_router(metering_admin_router, prefix=api_prefix)
    
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
        from .db.session import get_db_connection
        
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