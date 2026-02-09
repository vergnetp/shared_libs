"""
create_service - One function to create a production-ready FastAPI service.

Everything explicit, no hidden config files, no magic.
Full IDE support with intellisense and type checking.

Example:
    from app_kernel import create_service
    
    app = create_service(
        name="my-api",
        database_url="postgresql://...",
        redis_url="redis://...",
        jwt_secret="my-32-char-secret-key-here-xxx",
        cors_origins=["https://myapp.com"],
        routers=[users_router, orders_router],
    )
"""

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import (
    Any, Callable, Dict, List, Optional, Sequence, Tuple, Union,
    Awaitable,
)
from urllib.parse import urlparse

from fastapi import FastAPI, APIRouter

from .env_checks import run_env_checks, get_env, is_prod, EnvCheck
from .bootstrap import ServiceConfig, create_service as _create_service_internal
from .saas.deps import get_or_create_personal_workspace


logger = logging.getLogger(__name__)


# =============================================================================
# Type Definitions
# =============================================================================

# Health check: async () -> (healthy, message)
HealthCheck = Tuple[str, Callable[[], Awaitable[Tuple[bool, str]]]]

# Task handler: async (payload, context) -> result
TaskHandler = Callable[[Any, Any], Awaitable[Any]]

# Env check: (settings) -> (passed, error_message)
EnvCheck = Callable[[Any], Tuple[bool, str]]

# Lifecycle hook
LifecycleHook = Callable[[], Awaitable[None]]

# Router definition: APIRouter or (prefix, router) or (prefix, router, tags)
RouterDef = Union[APIRouter, Tuple[str, APIRouter], Tuple[str, APIRouter, List[str]]]


# =============================================================================
# Main Function
# =============================================================================

def create_service(
    name: str,
    *,
    version: str = "1.0.0",
    description: str = "",
    api_prefix: str = "/api/v1",
    
    # === Core ===
    routers: Sequence[RouterDef] = (),
    tasks: Optional[Dict[str, TaskHandler]] = None,
    
    # === Infrastructure ===
    database_url: Optional[str] = None,
    redis_url: Optional[str] = None,
    schema_init: Optional[Callable] = None,
    
    # === Auth ===
    jwt_secret: Optional[str] = None,
    allow_self_signup: bool = True,
    
    # === Rate Limiting (requests per minute) ===
    rate_limit_anonymous_rpm: int = 30,
    rate_limit_authenticated_rpm: int = 120,
    rate_limit_admin_rpm: int = 600,
    
    # === CORS ===
    cors_origins: Optional[List[str]] = None,
    
    # === OAuth ===
    oauth_google: Optional[Tuple[str, str]] = None,
    oauth_github: Optional[Tuple[str, str]] = None,
    
    # === Billing ===
    stripe_secret_key: Optional[str] = None,
    stripe_webhook_secret: Optional[str] = None,
    seed_billing: Optional[Callable] = None,  # async (db, billing) -> None
    
    # === Email ===
    smtp_url: Optional[str] = None,
    email_from: Optional[str] = None,
    
    # === Environment Checks (app-specific) ===
    env_checks: Optional[List[EnvCheck]] = None,
    
    # === Health & Lifecycle ===
    health_checks: Sequence[HealthCheck] = (),
    on_startup: Optional[LifecycleHook] = None,
    on_shutdown: Optional[LifecycleHook] = None,
    
    # === Testing ===
    test_runners: Optional[List[Callable]] = None,
    
    # === Debug ===
    debug: bool = False,
    
    # === Database Seed ===
    db_seed: Optional[Callable] = None,
) -> FastAPI:
    """
    Create a production-ready FastAPI service.
    
    Everything is explicit - no hidden config, no env var magic (except ENV).
    IDE provides full intellisense. One function, one docstring.
    
    Args:
        name: Service name (used in logs, metrics, health)
        version: Service version (shown in /health)
        description: API description (shown in /docs)
        api_prefix: Prefix for app routers (default: /api/v1)
        
        routers: List of APIRouters to mount. Accepts:
            - APIRouter (mounted at api_prefix)
            - (prefix, router) tuple
            - (prefix, router, tags) tuple
        
        tasks: Background job handlers. Dict of task_name -> async handler.
            Requires redis_url. Workers run embedded.
        
        database_url: Database connection string. Required in prod.
            - postgresql://user:pass@host:5432/db
            - mysql://user:pass@host:3306/db
            - sqlite:///./data/app.db (dev only)
        
        redis_url: Redis connection string. Required in prod.
            - redis://localhost:6379
            - In dev: auto-starts Docker or uses fakeredis
        
        schema_init: Async function(db) to init app tables.
            Kernel tables (users, workspaces, audit, etc.) auto-created.
        
        db_seed: Async function(db) to seed initial data.
            Called after schema_init, only if tables were just created.
            Use for default admin user, initial config, etc.
        
        jwt_secret: JWT signing secret. Required in prod (32+ chars).
            Used for auth tokens.
        
        allow_self_signup: Allow open registration at /auth/register.
            True = anyone can register (B2C)
            False = invite-only (B2B)
        
        rate_limit_*_rpm: Requests per minute by user type.
            Override per-route with @rate_limit(n) decorator.
            Exclude routes with @no_rate_limit decorator.
        
        cors_origins: Allowed CORS origins. Required in prod (explicit list).
            Example: ["https://myapp.com", "https://admin.myapp.com"]
        
        oauth_google: Google OAuth credentials (client_id, client_secret).
            Enables /auth/oauth/google routes.
        
        oauth_github: GitHub OAuth credentials (client_id, client_secret).
            Enables /auth/oauth/github routes.
        
        stripe_secret_key: Stripe API secret key.
            Auto-mounts billing router at /api/v1/billing with:
            - Product/price listing
            - Subscription management
            - Checkout sessions
            - Admin routes for viewing all orders
        
        stripe_webhook_secret: Stripe webhook signing secret (optional).
            Enables webhook signature verification.
        
        seed_billing: Billing seed function async (db, billing) -> None.
            Called at startup to seed products/prices.
            BillingService instance injected automatically.
            
            Example:
                async def seed_billing(db, billing):
                    pro = await billing.create_product(db,
                        name="Pro", slug="pro", features=["api_access"])
                    await billing.create_price(db,
                        product_id=pro["id"], amount_cents=1999, interval="month")
                # Stripe sync happens automatically after seed_billing returns
        
        smtp_url: SMTP server URL for sending emails.
            Format: smtp://user:pass@host:587
            Requires email_from to be set.
        
        email_from: Sender address for emails.
            Format: "My App <noreply@myapp.com>"
            Requires smtp_url to be set.
        
        env_checks: App-specific environment checks.
            Each check: (settings) -> (passed, error_message)
            Run at startup. In prod, failures block startup.
            
            Example:
                def check_api_key(settings):
                    if not settings.stripe_secret_key:
                        return False, "Stripe key required"
                    return True, ""
        
        health_checks: List of (name, async_check_fn) for /health/ready.
            Each check returns (healthy: bool, message: str).
        
        on_startup: Async function called after DB init and seed.
        on_shutdown: Async function called before DB close.
        
        test_runners: List of test runner functions for self-testing.
            Each runner: async (base_url, auth_token) -> AsyncIterator[str]
            Auto-mounts POST /test/{runner-name} (admin only, SSE).
        
        debug: Enable debug mode. Forced False in prod.
    
    Returns:
        Configured FastAPI application.
    
    Auto-enabled features (no config needed):
        - SaaS: Workspaces, teams, personal workspace on signup
        - Audit: All DB changes logged (async via Redis)
        - Metering: Request counts, latency (async via Redis)
        - Tracing: Admin telemetry dashboard
        - Health: /health, /health/ready endpoints
        - Metrics: /metrics endpoint
        - Rate limiting: Global middleware with tiers
        - Admin worker: Embedded, consumes from Redis
    
    Auto-mounted routes:
        GET  /health         - Liveness probe
        GET  /health/ready   - Readiness (runs health_checks)
        GET  /metrics        - Prometheus metrics
        POST /auth/register  - Register (if allow_self_signup)
        POST /auth/login     - Login
        GET  /auth/me        - Current user
        *    /api/v1/*       - Your routers
        *    /workspaces/*   - Workspace CRUD
    
    Example - Minimal (dev):
        app = create_service(
            name="my-api",
            routers=[my_router],
        )
    
    Example - Production:
        app = create_service(
            name="my-api",
            version="2.1.0",
            
            # Infrastructure
            database_url="postgresql://user:pass@db:5432/myapp",
            redis_url="redis://redis:6379",
            
            # Auth
            jwt_secret="my-very-long-secret-key-at-least-32-chars",
            cors_origins=["https://myapp.com"],
            
            # Routes & Jobs
            routers=[users_router, orders_router],
            tasks={
                "send_email": send_email_handler,
                "process_order": process_order_handler,
            },
            
            # Schema & Seed
            schema_init=init_app_tables,
            db_seed=seed_admin_user,
            
            # Optional integrations
            oauth_google=("client_id", "client_secret"),
            stripe_secret_key="sk_live_...",
            smtp_url="smtp://user:pass@smtp.example.com:587",
            email_from="My App <noreply@myapp.com>",
            
            # App-specific checks
            env_checks=[check_external_api],
            
            # Health
            health_checks=[
                ("postgres", check_db),
                ("redis", check_redis),
            ],
        )
    """
    # Build settings namespace for env checks
    settings = SimpleNamespace(
        name=name,
        version=version,
        database_url=database_url,
        redis_url=redis_url,
        jwt_secret=jwt_secret,
        cors_origins=cors_origins,
        smtp_url=smtp_url,
        email_from=email_from,
        stripe_secret_key=stripe_secret_key,
        debug=debug,
    )
    
    # Force debug=False in prod
    if is_prod():
        debug = False
    
    # Run environment checks (raises in prod if failures)
    run_env_checks(settings, extra_checks=env_checks)
    
    # Log environment
    logger.info(f"Starting {name} v{version} (ENV={get_env().upper()})")
    
    # =========================================================================
    # Build internal config for bootstrap compatibility
    # =========================================================================
    
    # Convert oauth tuples to dict format
    oauth_providers = {}
    if oauth_google:
        oauth_providers["google"] = {
            "client_id": oauth_google[0],
            "client_secret": oauth_google[1],
        }
    if oauth_github:
        oauth_providers["github"] = {
            "client_id": oauth_github[0],
            "client_secret": oauth_github[1],
        }
    
    # Build internal config
    config = ServiceConfig(
        jwt_secret=jwt_secret or "dev-secret-change-me",
        auth_enabled=True,
        allow_self_signup=allow_self_signup,
        saas_enabled=True,
        oauth_providers=oauth_providers,
        redis_url=redis_url,
        database_url=database_url,
        cors_origins=cors_origins or ["*"],
        cors_credentials=True,
        rate_limit_enabled=True,
        rate_limit_requests=rate_limit_authenticated_rpm,
        rate_limit_window=60,
        email_enabled=bool(smtp_url and email_from),
        email_from=email_from,
        **_parse_smtp_url(smtp_url) if smtp_url else {},
        debug=debug,
        log_level="DEBUG" if debug else "INFO",
        request_metrics_enabled=True,
        tracing_enabled=True,
        admin_worker_embedded=True,
    )
    
    # =========================================================================
    # Billing Setup
    # =========================================================================
    billing_router = None
    billing_service = None
    stripe_sync = None
    
    if stripe_secret_key:
        try:
            from billing import BillingConfig, BillingService, StripeSync, WebhookHandler
            from .integrations.billing import create_billing_router
            from .db.session import db_context
            from .auth import get_current_user, require_admin as _require_admin
            
            billing_config = BillingConfig(
                stripe_secret_key=stripe_secret_key,
                stripe_webhook_secret=stripe_webhook_secret,
            )
            billing_service = BillingService(billing_config)
            stripe_sync = StripeSync(billing_config)
            webhook_handler = WebhookHandler(billing_config)
            
            # Router created in kernel (not billing module)
            billing_router = create_billing_router(
                billing_service=billing_service,
                stripe_sync=stripe_sync,
                webhook_handler=webhook_handler,
                billing_config=billing_config,
                get_db_connection=db_context,
                require_auth=get_current_user,
                require_admin=_require_admin,
                prefix="/billing",
            )
            
            logger.info("Billing enabled: /api/v1/billing routes mounted (with admin routes)")
        except ImportError:
            logger.warning("stripe_secret_key provided but billing module not installed")
    
    # Wrap schema_init to include db_seed and seed_billing
    original_schema_init = schema_init
    
    async def _schema_init_with_seed(db):
        """Run schema init, then db_seed, then seed_billing + sync."""
        if original_schema_init:
            await original_schema_init(db)
        if db_seed:
            await db_seed(db)
        
        # Billing seed: call user's seed_billing(db, billing), then sync to Stripe
        if seed_billing and billing_service:
            await seed_billing(db, billing_service)
            
            # Auto-sync all products and prices to Stripe
            if stripe_sync:
                products = await billing_service.list_products(db, active_only=False)
                for product in products:
                    await stripe_sync.sync_product(db, billing_service, product=product)
                    prices = await billing_service.list_prices(db, product_id=product["id"], active_only=False)
                    for price in prices:
                        await stripe_sync.sync_price(db, billing_service, price=price, product=product)
                logger.info(f"Synced {len(products)} products to Stripe")
    
    # Create on_signup handler for personal workspace
    async def _on_signup(user_id: str, email: str):
        await get_or_create_personal_workspace(user_id, email)
    
    # Combine routers
    all_routers = list(routers)
    if billing_router:
        all_routers.append(billing_router)
    
    # Call internal create_service
    return _create_service_internal(
        name=name,
        version=version,
        description=description,
        routers=all_routers,
        tasks=tasks,
        config=config,
        schema_init=_schema_init_with_seed if (schema_init or db_seed or seed_billing) else None,
        health_checks=health_checks,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        api_prefix=api_prefix,
        test_runners=test_runners,
    )


def _parse_smtp_url(url: str) -> dict:
    """Parse smtp://user:pass@host:port into config dict."""
    from urllib.parse import urlparse
    
    if not url:
        return {}
    
    parsed = urlparse(url)
    
    return {
        "smtp_host": parsed.hostname,
        "smtp_port": parsed.port or 587,
        "smtp_user": parsed.username,
        "smtp_password": parsed.password,
        "smtp_use_tls": True,
    }
