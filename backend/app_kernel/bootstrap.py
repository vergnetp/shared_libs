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
            database_name=os.environ.get("database_name"),
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
    TracingSettings,
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
    
    # SaaS (workspaces, members, invites)
    saas_enabled: bool = False
    saas_invite_base_url: Optional[str] = None  # e.g., "https://app.example.com/invite"
    
    # Redis (optional - enables jobs, rate limiting, idempotency)
    redis_url: Optional[str] = None
    redis_key_prefix: str = "queue:"  # Match job_queue default
    
    # Database (kernel manages connection pool, app provides schema)
    database_name: Optional[str] = None  # DB name or file path for sqlite
    database_type: str = "sqlite"        # sqlite, postgres, mysql
    database_host: str = "localhost"
    database_port: Optional[int] = None  # None = use default for type
    database_user: Optional[str] = None
    database_password: Optional[str] = None
    
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
    def from_env(cls, prefix: str = "") -> "ServiceConfig":
        """
        Load config from environment variables.
        
        Args:
            prefix: Optional prefix for env vars (e.g., "MY_APP_")
        
        Environment variables:
            # Auth
            {prefix}JWT_SECRET: Required for production
            {prefix}AUTH_ENABLED: Enable auth (default: true)
            {prefix}ALLOW_SELF_SIGNUP: Allow self-registration (default: false)
            
            # SaaS
            {prefix}SAAS_ENABLED: Enable workspaces/teams (default: false)
            {prefix}SAAS_INVITE_BASE_URL: Base URL for invite links
            
            # Database
            {prefix}DATABASE_NAME: DB name or file path
            {prefix}DATABASE_TYPE: sqlite, postgres, mysql (default: sqlite)
            {prefix}DATABASE_HOST: DB host (default: localhost)
            {prefix}DATABASE_PORT: DB port
            {prefix}DATABASE_USER: DB user
            {prefix}DATABASE_PASSWORD: DB password
            
            # Redis
            {prefix}REDIS_URL: Enables jobs, rate limiting
            {prefix}REDIS_KEY_PREFIX: Key prefix (default: queue:)
            
            # Email
            {prefix}EMAIL_ENABLED: Enable email (default: false)
            {prefix}EMAIL_PROVIDER: smtp, ses, sendgrid (default: smtp)
            {prefix}EMAIL_FROM: Sender address
            {prefix}EMAIL_REPLY_TO: Reply-to address
            {prefix}SMTP_HOST: SMTP server host
            {prefix}SMTP_PORT: SMTP port (default: 587)
            {prefix}SMTP_USER: SMTP username
            {prefix}SMTP_PASSWORD: SMTP password
            {prefix}SMTP_USE_TLS: Use TLS (default: true)
            
            # Other
            {prefix}CORS_ORIGINS: Comma-separated origins
            {prefix}DEBUG: Enable debug mode
            {prefix}LOG_LEVEL: Logging level (default: INFO)
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
            saas_enabled=env_bool("SAAS_ENABLED", False),
            saas_invite_base_url=env("SAAS_INVITE_BASE_URL"),
            redis_url=env("REDIS_URL"),
            redis_key_prefix=env("REDIS_KEY_PREFIX", "queue:"),
            database_name=env("DATABASE_NAME"),
            database_type=env("DATABASE_TYPE", "sqlite"),
            database_host=env("DATABASE_HOST", "localhost"),
            database_port=env_int("DATABASE_PORT", 0) or None,
            database_user=env("DATABASE_USER"),
            database_password=env("DATABASE_PASSWORD"),
            cors_origins=env_list("CORS_ORIGINS", ["*"]),
            cors_credentials=env_bool("CORS_CREDENTIALS", True),
            rate_limit_enabled=env_bool("RATE_LIMIT_ENABLED", True),
            rate_limit_requests=env_int("RATE_LIMIT_REQUESTS", 100),
            rate_limit_window=env_int("RATE_LIMIT_WINDOW", 60),
            max_concurrent_streams=env_int("MAX_CONCURRENT_STREAMS", 3),
            stream_lease_ttl=env_int("STREAM_LEASE_TTL", 300),
            worker_count=env_int("WORKER_COUNT", 4),
            job_max_attempts=env_int("JOB_MAX_ATTEMPTS", 3),
            # Email
            email_enabled=env_bool("EMAIL_ENABLED", False),
            email_provider=env("EMAIL_PROVIDER", "smtp"),
            email_from=env("EMAIL_FROM"),
            email_reply_to=env("EMAIL_REPLY_TO"),
            smtp_host=env("SMTP_HOST"),
            smtp_port=env_int("SMTP_PORT", 587),
            smtp_user=env("SMTP_USER"),
            smtp_password=env("SMTP_PASSWORD"),
            smtp_use_tls=env_bool("SMTP_USE_TLS", True),
            # Debug
            debug=env_bool("DEBUG", False),
            log_level=env("LOG_LEVEL", "INFO"),
            # Request Metrics
            request_metrics_enabled=env_bool("REQUEST_METRICS_ENABLED", False),
            request_metrics_exclude_paths=env_list("REQUEST_METRICS_EXCLUDE_PATHS", [
                "/health", "/healthz", "/readyz", "/metrics", "/favicon.ico"
            ]),
            # Tracing - enabled by default
            tracing_enabled=env_bool("TRACING_ENABLED", True),
            tracing_sample_rate=float(env("TRACING_SAMPLE_RATE", "1.0")),
        )
    
    @classmethod
    def from_manifest(cls, manifest_path: str = "manifest.yaml") -> "ServiceConfig":
        """
        Load config from manifest.yaml with env var interpolation.
        
        Manifest values can use ${ENV_VAR} or ${ENV_VAR:-default} syntax.
        Environment variables override manifest values.
        
        Args:
            manifest_path: Path to manifest.yaml
            
        Example manifest.yaml:
            name: my-service
            version: "1.0.0"
            
            database:
              type: sqlite
              path: ${DATABASE_PATH:-./data/app.db}
            
            redis:
              url: ${REDIS_URL}
              key_prefix: "myapp:"
            
            auth:
              jwt_secret: ${JWT_SECRET}
              allow_self_signup: false
            
            saas:
              enabled: true
              invite_base_url: ${SAAS_INVITE_BASE_URL}
            
            email:
              enabled: ${EMAIL_ENABLED:-false}
              from: ${EMAIL_FROM:-noreply@example.com}
              smtp_host: ${SMTP_HOST}
              smtp_port: 587
              smtp_user: ${SMTP_USER}
              smtp_password: ${SMTP_PASSWORD}
        """
        import re
        import yaml
        from pathlib import Path
        
        manifest_file = Path(manifest_path)
        if not manifest_file.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest_path}")
        
        with open(manifest_file) as f:
            manifest = yaml.safe_load(f)
        
        def interpolate(value: Any) -> Any:
            """Interpolate ${ENV_VAR} and ${ENV_VAR:-default} in strings."""
            if not isinstance(value, str):
                return value
            
            # Pattern: ${VAR} or ${VAR:-default}
            pattern = r'\$\{([^}:]+)(?::-([^}]*))?\}'
            
            def replacer(match):
                var_name = match.group(1)
                default = match.group(2)
                return os.environ.get(var_name, default if default is not None else "")
            
            result = re.sub(pattern, replacer, value)
            
            # Convert string bools
            if result.lower() in ("true", "yes", "1"):
                return True
            if result.lower() in ("false", "no", "0"):
                return False
            
            # Try to convert to int
            try:
                return int(result)
            except (ValueError, TypeError):
                pass
            
            return result if result else None
        
        def get_nested(d: dict, *keys, default=None):
            """Get nested dict value with interpolation."""
            for key in keys:
                if not isinstance(d, dict):
                    return default
                d = d.get(key, default)
                if d is default:
                    return default
            return interpolate(d) if d is not None else default
        
        # Extract config sections
        db = manifest.get("database", {})
        redis = manifest.get("redis", {})
        auth = manifest.get("auth", {})
        saas = manifest.get("saas", {})
        email = manifest.get("email", {})
        cors = manifest.get("cors", {})
        jobs = manifest.get("jobs", {})
        
        # Helper: apply default AFTER interpolation (fixes ${VAR} without env set)
        def _default(val, default):
            """Return default if val is None. Handles interpolated empty strings."""
            return default if val is None else val
        
        return cls(
            # Auth
            jwt_secret=interpolate(auth.get("jwt_secret")) or "dev-secret-change-me",
            jwt_expiry_hours=_default(interpolate(auth.get("jwt_expiry_hours")), 24),
            auth_enabled=_default(interpolate(auth.get("enabled")), True),
            allow_self_signup=_default(interpolate(auth.get("allow_self_signup")), False),
            
            # SaaS
            saas_enabled=_default(interpolate(saas.get("enabled")), False),
            saas_invite_base_url=interpolate(saas.get("invite_base_url")),
            
            # Redis
            redis_url=interpolate(redis.get("url")),
            redis_key_prefix=interpolate(redis.get("key_prefix")) or "queue:",
            
            # Database
            database_name=interpolate(db.get("path") or db.get("name")),
            database_type=interpolate(db.get("type")) or "sqlite",
            database_host=interpolate(db.get("host")) or "localhost",
            database_port=interpolate(db.get("port")),
            database_user=interpolate(db.get("user")),
            database_password=interpolate(db.get("password")),
            
            # CORS
            cors_origins=interpolate(cors.get("origins")) or ["*"],
            cors_credentials=_default(interpolate(cors.get("credentials")), True),
            
            # Jobs
            worker_count=_default(interpolate(jobs.get("worker_count")), 4),
            job_max_attempts=_default(interpolate(jobs.get("max_attempts")), 3),
            
            # Email
            email_enabled=_default(interpolate(email.get("enabled")), False),
            email_provider=interpolate(email.get("provider")) or "smtp",
            email_from=interpolate(email.get("from")),
            email_reply_to=interpolate(email.get("reply_to")),
            smtp_host=interpolate(email.get("smtp_host")),
            smtp_port=_default(interpolate(email.get("smtp_port")), 587),
            smtp_user=interpolate(email.get("smtp_user")),
            smtp_password=interpolate(email.get("smtp_password")),
            smtp_use_tls=_default(interpolate(email.get("smtp_use_tls")), True),
            
            # Debug
            debug=_default(interpolate(manifest.get("debug")), False),
            log_level=interpolate(manifest.get("log_level")) or "INFO",
            # Request Metrics (from observability section)
            request_metrics_enabled=_default(interpolate(
                manifest.get("observability", {}).get("request_metrics", {}).get("enabled")
            ), False),
            request_metrics_exclude_paths=interpolate(
                manifest.get("observability", {}).get("request_metrics", {}).get("exclude_paths")
            ) or ["/health", "/healthz", "/readyz", "/metrics", "/favicon.ico"],
            # Tracing (from tracing section) - enabled by default
            tracing_enabled=_default(interpolate(
                manifest.get("tracing", {}).get("enabled")
            ), True),
            tracing_sample_rate=float(_default(interpolate(
                manifest.get("tracing", {}).get("sample_rate")
            ), 1.0)),
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
    manifest_path: Optional[str] = None,  # Path to manifest.yaml for auto-wiring
    
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
    auth_service: Optional[Callable] = None,
    user_store: Optional[Any] = None,  # Direct UserStore implementation
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
        manifest_path: Path to manifest.yaml for auto-wiring integrations
            When provided, kernel auto-configures:
            - billing: section → billing routes + tasks
        schema_init: Async function(db) to initialize app database tables
        version: Service version
        description: API description
        on_startup: Async function called on startup (after db init)
        on_shutdown: Async function called on shutdown (before db close)
        health_checks: List of (name, check_fn) for /readyz
        auth_service: Factory function for auth service (enables login/register)
        is_admin: Function(user) -> bool for admin checks
        api_prefix: Prefix for app routers (default: /api/v1)
        docs_url: OpenAPI docs URL
        redoc_url: ReDoc URL
    
    Returns:
        Configured FastAPI application
    
    Example:
        # Simple - just config
        app = create_service(
            name="widget_service",
            routers=[widgets_router],
            config=ServiceConfig.from_env(),
        )
        
        # Full manifest with auto-wiring (billing, etc.)
        app = create_service(
            name="my-saas",
            routers=[my_router],
            config=ServiceConfig.from_manifest("manifest.yaml"),
            manifest_path="manifest.yaml",  # Enables auto-wiring
            schema_init=init_tables,
        )
    """
    # Load .env hierarchy first (before any config loading)
    # This ensures env vars are available for ServiceConfig and manifest interpolation
    from .env import load_env_hierarchy
    
    if manifest_path:
        # Use manifest location to determine service directory
        from pathlib import Path
        service_dir = str(Path(manifest_path).parent.resolve())
        load_env_hierarchy(service_dir=service_dir)
    else:
        # Fallback: load from current working directory
        load_env_hierarchy()
    
    # Use provided config or load from env
    cfg = config or ServiceConfig.from_env()
    
    # Read manifest for auto-wiring integrations
    manifest = None
    if manifest_path:
        import yaml
        import re
        from pathlib import Path
        
        manifest_file = Path(manifest_path)
        if manifest_file.exists():
            with open(manifest_file) as f:
                content = f.read()
            
            # Interpolate env vars
            def _interpolate(match):
                var_name = match.group(1)
                default = match.group(2)
                return os.environ.get(var_name, default if default is not None else "")
            
            content = re.sub(r'\$\{([^}:]+)(?::-([^}]*))?\}', _interpolate, content)
            manifest = yaml.safe_load(content)
    
    # Collect additional routers/tasks from integrations
    integration_routers = []
    integration_tasks = {}
    billing_enabled = False
    
    # Setup billing integration if billing: section exists in manifest
    if manifest and manifest.get("billing"):
        from .db import get_db_connection
        from .auth.deps import require_auth
        from .integrations.billing import setup_kernel_billing
        
        billing_router, billing_tasks = setup_kernel_billing(
            manifest["billing"],
            get_db_connection,
            require_auth,
        )
        
        if billing_router:
            integration_routers.append(billing_router)
            integration_tasks.update(billing_tasks)
            billing_enabled = True
    
    # Setup request metrics task if enabled (requires Redis for async storage)
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
    kernel_settings = _build_kernel_settings(name, version, cfg, health_checks)
    
    # Create lifespan
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from .observability import get_logger, get_metrics
        logger = get_logger()
        metrics = get_metrics()
        
        # Initialize database if configured
        if cfg.database_name:
            from .db import init_db_session, init_schema, get_db_connection
            
            # Ensure data directory exists for SQLite
            if cfg.database_type == "sqlite":
                from pathlib import Path
                Path(cfg.database_name).parent.mkdir(parents=True, exist_ok=True)
                init_db_session(
                    database_name=cfg.database_name,
                    database_type="sqlite",
                )
            else:
                init_db_session(
                    database_name=cfg.database_name,
                    database_type=cfg.database_type,
                    host=cfg.database_host,
                    port=cfg.database_port or 5432,
                    user=cfg.database_user,
                    password=cfg.database_password,
                )
            logger.info(f"Database initialized", extra={
                "type": cfg.database_type,
                "database": cfg.database_name,
            })
            
            # Initialize AUTH schema if auth enabled (before app schema)
            if cfg.auth_enabled:
                from .auth.schema import init_auth_schema
                await init_schema(init_auth_schema)
                logger.info("Auth schema initialized")
            
            # Initialize SAAS schema if saas enabled
            if cfg.saas_enabled:
                from .db.schema import init_saas_schema
                await init_schema(init_saas_schema)
                logger.info("SaaS schema initialized (workspaces, members, invites)")
            
            # Initialize request metrics schema if enabled
            if request_metrics_enabled:
                from .observability.request_metrics import RequestMetricsStore
                await init_schema(RequestMetricsStore.init_schema)
                logger.info("Request metrics schema initialized")
            
            # Initialize app schema if provided
            if schema_init:
                await init_schema(schema_init)
                logger.info("Database schema initialized")
        
        # Setup email integration (if enabled)
        if cfg.email_enabled:
            from .integrations.email import setup_kernel_email
            if setup_kernel_email(cfg):
                logger.info(f"Email configured: {cfg.smtp_host}:{cfg.smtp_port}")
            else:
                logger.warning("Email enabled but setup failed - check SMTP settings")
        
        # Setup billing catalog (seed products/prices from manifest)
        if billing_enabled and manifest_path:
            from .integrations.billing import seed_billing_catalog
            from .db import get_db_connection
            
            try:
                # Initialize billing tables first
                try:
                    from ..billing.services import BillingService
                    from .db import init_schema as run_init_schema
                    
                    async def init_billing_schema(db):
                        """Create billing tables."""
                        await db.execute("""
                            CREATE TABLE IF NOT EXISTS billing_product (
                                id TEXT PRIMARY KEY,
                                name TEXT NOT NULL,
                                slug TEXT UNIQUE NOT NULL,
                                description TEXT,
                                features TEXT,
                                metadata TEXT,
                                active INTEGER DEFAULT 1,
                                product_type TEXT DEFAULT 'subscription',
                                shippable INTEGER DEFAULT 0,
                                stripe_product_id TEXT,
                                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                        await db.execute("""
                            CREATE TABLE IF NOT EXISTS billing_price (
                                id TEXT PRIMARY KEY,
                                product_id TEXT NOT NULL,
                                amount_cents INTEGER NOT NULL,
                                currency TEXT DEFAULT 'usd',
                                interval TEXT,
                                interval_count INTEGER DEFAULT 1,
                                nickname TEXT,
                                metadata TEXT,
                                active INTEGER DEFAULT 1,
                                stripe_price_id TEXT,
                                created_at TEXT DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                        await db.execute("""
                            CREATE TABLE IF NOT EXISTS billing_customer (
                                id TEXT PRIMARY KEY,
                                user_id TEXT UNIQUE NOT NULL,
                                email TEXT NOT NULL,
                                name TEXT,
                                metadata TEXT,
                                stripe_customer_id TEXT,
                                created_at TEXT DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                        await db.execute("""
                            CREATE TABLE IF NOT EXISTS billing_subscription (
                                id TEXT PRIMARY KEY,
                                customer_id TEXT NOT NULL,
                                price_id TEXT NOT NULL,
                                status TEXT DEFAULT 'active',
                                current_period_start TEXT,
                                current_period_end TEXT,
                                trial_end TEXT,
                                cancel_at_period_end INTEGER DEFAULT 0,
                                cancelled_at TEXT,
                                metadata TEXT,
                                stripe_subscription_id TEXT,
                                created_at TEXT DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                        await db.execute("""
                            CREATE TABLE IF NOT EXISTS billing_invoice (
                                id TEXT PRIMARY KEY,
                                customer_id TEXT,
                                subscription_id TEXT,
                                stripe_invoice_id TEXT,
                                status TEXT,
                                amount_due INTEGER,
                                amount_paid INTEGER,
                                currency TEXT,
                                invoice_pdf TEXT,
                                hosted_invoice_url TEXT,
                                period_start TEXT,
                                period_end TEXT,
                                created_at TEXT DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                        await db.execute("""
                            CREATE TABLE IF NOT EXISTS billing_payment_method (
                                id TEXT PRIMARY KEY,
                                customer_id TEXT NOT NULL,
                                stripe_payment_method_id TEXT,
                                type TEXT,
                                card_last4 TEXT,
                                card_brand TEXT,
                                is_default INTEGER DEFAULT 0,
                                created_at TEXT DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                        await db.execute("""
                            CREATE TABLE IF NOT EXISTS billing_order (
                                id TEXT PRIMARY KEY,
                                customer_id TEXT NOT NULL,
                                price_id TEXT NOT NULL,
                                product_id TEXT NOT NULL,
                                quantity INTEGER DEFAULT 1,
                                amount_cents INTEGER NOT NULL,
                                currency TEXT DEFAULT 'usd',
                                status TEXT DEFAULT 'pending',
                                product_type TEXT,
                                shipping_address TEXT,
                                tracking_number TEXT,
                                shipped_at TEXT,
                                delivered_at TEXT,
                                stripe_payment_intent_id TEXT,
                                stripe_checkout_session_id TEXT,
                                metadata TEXT,
                                created_at TEXT DEFAULT CURRENT_TIMESTAMP
                            )
                        """)
                    
                    await run_init_schema(init_billing_schema)
                    logger.info("Billing schema initialized")
                except Exception as e:
                    logger.warning(f"Billing schema init skipped: {e}")
                
                # Seed catalog
                result = await seed_billing_catalog(manifest_path, get_db_connection)
                if result.get("products_created"):
                    logger.info(f"Billing: seeded {len(result['products_created'])} products")
            except Exception as e:
                logger.error(f"Billing catalog seed failed: {e}")
        
        # Run app startup hook
        if on_startup:
            await on_startup()
        
        logger.info(f"{name} starting", extra={
            "version": version,
            "debug": cfg.debug,
            "redis": bool(cfg.redis_url),
            "database": bool(cfg.database_name),
        })
        metrics.set_gauge("service_started", 1)
        
        yield
        
        # Run app shutdown hook
        if on_shutdown:
            await on_shutdown()
        
        # Close database
        if cfg.database_name:
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
    
    # Get user store - either direct, from auth_service adapter, or auto-create
    _user_store = user_store  # Direct user_store takes precedence
    if _user_store is None and auth_service is not None:
        from .auth import AuthServiceAdapter
        _user_store = AuthServiceAdapter(auth_service())
    
    # Auto-create user store if DB + auth enabled and no store provided
    if _user_store is None and cfg.database_name and cfg.auth_enabled:
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
            enable_saas_routes=cfg.saas_enabled,
            saas_invite_base_url=cfg.saas_invite_base_url,
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
