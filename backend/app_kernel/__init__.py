"""
app_kernel - Runtime infrastructure for backend services.

This module provides a stable, reusable application kernel that can be
used across multiple backend services. It handles:

- Auth primitives (user identity, admin check)
- Workspace/access primitives (membership checks)
- DB session/connection factory
- Job engine wrapper (enqueue + worker dispatch)
- Streaming lifecycle safety (Redis leases)
- Reliability middleware (rate limiting, idempotency)
- Observability (structured logging, metrics, audit)
- Auto-mounted routers (health, metrics, auth)

Philosophy:
- app_kernel provides MECHANISMS and INVARIANTS
- Apps provide MEANING and BUSINESS LOGIC
- app_kernel is domain-agnostic
- All configuration is immutable after initialization

IMPORTANT: Workers are separate processes, not part of FastAPI lifecycle.
The kernel provides worker code; deployment decides how to run workers.

Usage (API process):
    from fastapi import FastAPI
    from app_kernel import init_app_kernel, KernelSettings
    from app_kernel.settings import AuthSettings, RedisSettings, FeatureSettings
    from app_kernel.jobs import JobRegistry
    
    app = FastAPI()
    
    # Create job registry
    registry = JobRegistry()
    
    @registry.task("process_document")
    async def process_document(payload, ctx):
        ...
    
    # Initialize kernel (auto-mounts health, metrics, auth routes)
    settings = KernelSettings(
        auth=AuthSettings(token_secret=os.environ["JWT_SECRET"]),
        redis=RedisSettings(url=os.environ["REDIS_URL"]),
        features=FeatureSettings(
            allow_self_signup=False,  # Important default
            protect_metrics="admin",  # Require admin for /metrics
        ),
    )
    
    init_app_kernel(app, settings, registry, user_store=my_user_store)
    
    # Workers run as separate processes - see jobs/worker.py
"""

__version__ = "1.0.0"

from .settings import (
    KernelSettings,
    RedisSettings,
    StreamingSettings,
    JobSettings,
    AuthSettings,
    ObservabilitySettings,
    TracingSettings,
    ReliabilitySettings,
    FeatureSettings,
    CorsSettings,
    SecuritySettings,
)

from .app import init_app_kernel, get_kernel, KernelRuntime, http_client

# HTTP client config (re-exported from http_client library)
from ..http_client.config import HttpConfig

# Environment loading (for .env file support in development)
from .env import load_env_hierarchy

# Re-export commonly used items
from .auth import (
    UserIdentity,
    get_current_user,
    get_current_user_optional,
    require_admin,
    require_auth,
    get_request_context,
    AuthError,
    UserStore,
    create_auth_router,
    AuthServiceAdapter,
)

from .jobs import (
    JobRegistry,
    JobContext,
    get_job_client,
    start_workers,
    stop_workers,
    run_worker,
    create_jobs_router,
)

from ..streaming import (
    stream_lease,
    StreamLimitExceeded,
    get_active_streams,
)

from .observability import (
    get_logger,
    log_context,
    get_metrics,
    get_audit,
    # Request metrics
    RequestMetric,
    RequestMetricsMiddleware,
    RequestMetricsStore,
    get_real_ip,
    get_geo_from_headers,
    setup_request_metrics,
    create_request_metrics_router,
)

from .reliability import (
    rate_limit,
    no_rate_limit,
    idempotent,
)

from .health import create_health_router

from .middleware import CacheBustedStaticFiles, get_traced_service_name

from .utils import (
    Profiler,
    profiled_function,
)

from .db import db_context

# SaaS - multi-tenant workspace/team functionality
from .saas import (
    WorkspaceStore,
    MemberStore,
    InviteStore,
    require_workspace_member,
    require_workspace_admin,
    require_workspace_owner,
    create_saas_router,
)

# Integrations - optional external module wrappers
from .integrations import (
    send_email,
    is_email_configured,
)

# Billing - optional, only available if billing module installed
try:
    from billing import BillingService, BillingConfig, StripeSync
    _billing_available = True
except ImportError:
    BillingService = None
    BillingConfig = None
    StripeSync = None
    _billing_available = False

# Bootstrap - service creation
from .create_service import create_service
from .bootstrap import (
    quick_service,
    ServiceConfig,  # Kept for backward compat
)

# Environment checks
from .env_checks import (
    EnvCheck,
    run_env_checks,
    get_env,
    is_prod,
    is_dev,
    is_staging,
    # Built-in checks (can be reused in app-specific checks)
    check_database_url,
    check_redis_url,
    check_jwt_secret,
    check_cors_origins,
    check_email_config,
    is_uat,
    is_test,
)

# Tasks - cancellable SSE-streamed operations
from .tasks import (
    TaskStream,
    TaskCancelled,
    Cancelled,  # Backwards compat alias
    create_tasks_router,
    sse_event,
    sse_task_id,
    sse_log,
    sse_complete,
    sse_urls,
)

__all__ = [
    # Version
    "__version__",
    
    # Main init
    "init_app_kernel",
    "get_kernel",
    "KernelRuntime",
    "http_client",
    "HttpConfig",
    
    # Service creation
    "create_service",
    "quick_service",
    "ServiceConfig",  # Backward compat
    
    # Environment checks
    "EnvCheck",
    "run_env_checks",
    "get_env",
    "is_prod",
    "is_dev",
    "is_staging",
    "is_uat",
    "is_test",
    "check_database_url",
    "check_redis_url",
    "check_jwt_secret",
    "check_cors_origins",
    "check_email_config",
    
    # Settings
    "KernelSettings",
    "RedisSettings", 
    "StreamingSettings",
    "JobSettings",
    "AuthSettings",
    "ObservabilitySettings",
    "TracingSettings",
    "ReliabilitySettings",
    "FeatureSettings",
    "CorsSettings",
    "SecuritySettings",
    
    # Environment
    "load_env_hierarchy",
    
    # Auth
    "UserIdentity",
    "get_current_user",
    "get_current_user_optional",
    "require_admin",
    "require_auth",
    "get_request_context",
    "AuthError",
    "UserStore",
    "create_auth_router",
    "AuthServiceAdapter",
    
    # Jobs
    "JobRegistry",
    "JobContext",
    "get_job_client",
    "start_workers",
    "stop_workers",
    "run_worker",
    "create_jobs_router",
    
    # Streaming
    "stream_lease",
    "StreamLimitExceeded",
    "get_active_streams",
    
    # Observability
    "get_logger",
    "log_context",
    "get_metrics",
    "get_audit",
    # Request metrics
    "RequestMetric",
    "RequestMetricsMiddleware",
    "RequestMetricsStore",
    "get_real_ip",
    "get_geo_from_headers",
    "setup_request_metrics",
    "create_request_metrics_router",
    
    # Reliability
    "rate_limit",
    "no_rate_limit",
    "idempotent",
    
    # Health
    "create_health_router",
    
    # Static files
    "CacheBustedStaticFiles",
    
    # Tracing
    "get_traced_service_name",
    
    # Database
    "db_context",
    
    # SaaS
    "WorkspaceStore",
    "MemberStore",
    "InviteStore",
    "require_workspace_member",
    "require_workspace_admin",
    "require_workspace_owner",
    "create_saas_router",
    
    # Integrations
    "send_email",
    "is_email_configured",
    
    # Billing (optional)
    "BillingService",
    "BillingConfig",
    "StripeSync",
    
    # Utils
    "Profiler",
    "profiled_function",
    
    # Tasks
    "TaskStream",
    "TaskCancelled",
    "Cancelled",  # Backwards compat alias
    "create_tasks_router",
    "sse_event",
    "sse_task_id",
    "sse_log",
    "sse_complete",
    "sse_urls",
]