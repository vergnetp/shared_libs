"""
Agent Service - AI Agents as a Service

Features:
- Multi-provider support (OpenAI, Anthropic, Ollama)
- Conversation management (threads, messages)
- Cost tracking and budgets
- RAG document processing
- Background job processing
- Rate limiting and idempotency

Database is managed by app_kernel - no duplicate initialization needed.
"""

from pathlib import Path
from typing import Tuple

# Import deps FIRST to set up sys.path
from .src import deps

from fastapi import Depends
from fastapi.staticfiles import StaticFiles

from backend.app_kernel import (
    create_service,
    ServiceConfig,
    get_audit,
    get_logger,
)
from backend.app_kernel.db import get_db_manager, get_db_session
from backend.app_kernel.jobs import create_jobs_router, get_job_client

from .config import get_settings
from .src.deps import init_app_dependencies, shutdown_app_dependencies
from .src.db_schema import init_agent_schema  # NEW: separated schema
from .src.jobs import registry
from .src.routes import (
    agents_router,
    threads_router,
    chat_router,
    documents_router,
    analytics_router,
    workspaces_router,
)
from .src.auth import get_auth_service, get_current_user
from .src.authz import require_admin, CurrentUser


# =============================================================================
# Health Checks
# =============================================================================

async def check_database() -> Tuple[bool, str]:
    """Health check for database connection."""
    try:
        db_manager = get_db_manager()
        async with db_manager as conn:
            await conn.execute("SELECT 1", ())
        return True, "database connected"
    except Exception as e:
        return False, f"database error: {e}"


async def check_vector_store() -> Tuple[bool, str]:
    """Health check for vector store."""
    try:
        from .src.deps import get_vector_store
        store = get_vector_store()
        if store is None:
            return True, "vector store not configured (optional)"
        return True, "vector store available"
    except Exception as e:
        return False, f"vector store error: {e}"


async def check_ai_models() -> Tuple[bool, str]:
    """Health check for AI models (embeddings, reranker)."""
    try:
        from .src.deps import get_ai_models_status
        status = get_ai_models_status()
        if status.get("ready"):
            return True, "AI models loaded"
        elif status.get("loading"):
            return True, "AI models loading (service operational)"
        return True, "AI models not loaded (optional)"
    except Exception as e:
        return True, f"AI models status unknown: {e}"


# =============================================================================
# Lifecycle Hooks
# =============================================================================

async def on_startup():
    """
    Initialize app-specific dependencies.
    
    NOTE: Database is already initialized by kernel at this point.
    We only init app-specific things here (attachments, cost tracker, etc).
    """
    settings = get_settings()
    
    # Ensure upload directory exists
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    
    # Initialize app-specific dependencies (NOT database)
    await init_app_dependencies(settings)
    
    logger = get_logger()
    logger.info("Agent service initialized", extra={
        "version": settings.service_version,
        "debug": settings.debug,
        "auth_enabled": settings.auth_enabled,
        "redis": bool(settings.redis_url),
    })


async def on_shutdown():
    """Cleanup on shutdown."""
    get_logger().info("Agent service shutting down")
    await shutdown_app_dependencies()


# =============================================================================
# Admin Check
# =============================================================================

def is_admin_user(user) -> bool:
    """Check if user is admin."""
    if user is None:
        return False
    role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
    return role == "admin"


# =============================================================================
# Build Config from Settings
# =============================================================================

def _build_config() -> ServiceConfig:
    """
    Build ServiceConfig from app settings.
    
    IMPORTANT: Include database fields so kernel manages DB lifecycle.
    """
    settings = get_settings()
    return ServiceConfig(
        # Auth
        jwt_secret=settings.jwt_secret,
        jwt_expiry_hours=settings.jwt_expiry_hours,
        auth_enabled=settings.auth_enabled,
        allow_self_signup=settings.allow_self_signup,
        
        # Redis
        redis_url=settings.redis_url,
        redis_key_prefix="agent:",
        
        # DATABASE - kernel manages this now
        database_name=settings.database_name,
        database_type=settings.database_type,
        database_host=settings.database_host,
        database_port=settings.database_port,
        database_user=settings.database_user,
        database_password=settings.database_password,
        
        # CORS
        cors_origins=list(settings.cors_origins),
        cors_credentials=True,
        
        # Rate limiting
        rate_limit_enabled=True,
        rate_limit_requests=settings.rate_limit_requests,
        rate_limit_window=settings.rate_limit_window,
        
        # Streaming
        max_concurrent_streams=settings.concurrent_streams,
        stream_lease_ttl=settings.stream_lease_ttl,
        
        # Workers
        worker_count=4,
        job_max_attempts=3,
        
        # Debug
        debug=settings.debug,
        log_level="DEBUG" if settings.debug else "INFO",
    )


# =============================================================================
# Create App
# =============================================================================

def create_app():
    """Create the FastAPI application."""
    settings = get_settings()
    
    # Build tasks dict from registry
    tasks = {name: registry.get(name) for name in registry}
    
    # Create app using bootstrap
    app = create_service(
        name="agent-service",
        version=settings.service_version,
        description=__doc__,
        
        # Business logic routers
        routers=[
            workspaces_router,
            agents_router,
            threads_router,
            chat_router,
            documents_router,
            analytics_router,
        ],
        
        # Background tasks
        tasks=tasks if tasks else None,
        
        # Configuration (includes database!)
        config=_build_config(),
        
        # Database schema - kernel calls this after DB init
        schema_init=init_agent_schema,
        
        # Lifecycle (runs AFTER db init, BEFORE db close)
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        
        # Health & Auth
        health_checks=[check_database, check_vector_store, check_ai_models],
        auth_service=get_auth_service,
        is_admin=is_admin_user,
    )
    
    # =========================================================================
    # App-specific additions (not handled by create_service)
    # =========================================================================
    
    # Jobs router with kernel's get_db dependency
    if settings.redis_url:
        from backend.app_kernel.db import db_session_dependency
        
        jobs_router = create_jobs_router(
            get_db=db_session_dependency,  # Use kernel's dependency
            get_job_client=get_job_client,
            prefix="/jobs",
            tags=["jobs"],
        )
        app.include_router(jobs_router, prefix="/api/v1")
    
    # Audit endpoint (admin only)
    @app.get("/api/v1/audit")
    async def get_audit_logs(
        current_user: CurrentUser = Depends(get_current_user),
        user_id: str = None,
        workspace_id: str = None,
        request_id: str = None,
        limit: int = 100,
    ):
        """Query audit logs. Admin only."""
        require_admin(current_user)
        audit = get_audit()
        return await audit.query(
            user_id=user_id,
            workspace_id=workspace_id,
            request_id=request_id,
            limit=limit,
        )
    
    # Static files (test UI) - must be last
    static_path = Path(__file__).parent / "static"
    if static_path.exists():
        app.mount("/", StaticFiles(directory=str(static_path), html=True), name="static")
    
    return app


# Create app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=settings.debug)
