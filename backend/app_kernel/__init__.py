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
    ReliabilitySettings,
    FeatureSettings,
    CorsSettings,
    SecuritySettings,
)

from .app import init_app_kernel, get_kernel, KernelRuntime

# Re-export commonly used items
from .auth import (
    UserIdentity,
    get_current_user,
    get_current_user_optional,
    require_admin,
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

from .streaming import (
    stream_lease,
    StreamLimitExceeded,
    get_active_streams,
)

from .observability import (
    get_logger,
    log_context,
    get_metrics,
    get_audit,
)

from .reliability import (
    rate_limit,
)

from .health import create_health_router

from .db import (
    init_kernel_schema,
    cleanup_expired_idempotency_keys,
    cleanup_old_rate_limits,
)

# Bootstrap - simplified service creation
from .bootstrap import (
    create_service,
    quick_service,
    ServiceConfig,
)

__all__ = [
    # Version
    "__version__",
    
    # Main init
    "init_app_kernel",
    "get_kernel",
    "KernelRuntime",
    
    # Bootstrap (simplified)
    "create_service",
    "quick_service",
    "ServiceConfig",
    
    # Settings
    "KernelSettings",
    "RedisSettings", 
    "StreamingSettings",
    "JobSettings",
    "AuthSettings",
    "ObservabilitySettings",
    "ReliabilitySettings",
    "FeatureSettings",
    "CorsSettings",
    "SecuritySettings",
    
    # Auth
    "UserIdentity",
    "get_current_user",
    "get_current_user_optional",
    "require_admin",
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
    
    # Reliability
    "rate_limit",
    
    # Health
    "create_health_router",
    
    # Schema
    "init_kernel_schema",
    "cleanup_expired_idempotency_keys",
    "cleanup_old_rate_limits",
]
