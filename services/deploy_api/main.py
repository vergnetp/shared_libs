"""
Deploy API - Deployment Management Service

API wrapper for the infra deployment system.
Provides REST endpoints for:
- Workspace (tenant) management
- Project configuration
- Service management
- Deployment triggering and status
- Credentials management

Uses app_kernel for:
- Authentication (JWT)
- Job queue (background deployments)
- Database (connection pool + schema init)
- Audit logging
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

from backend.app_kernel import (
    create_service,
    ServiceConfig,
    get_logger,
)
from backend.app_kernel.access import workspace_access

from .config import get_settings
from ._gen.db_schema import init_schema
from .src.access import DeployWorkspaceChecker
from .src.routes import (
    workspaces_router,
    projects_router,
    deployments_router,
)
from .src.workers import TASKS


# Static files directory
STATIC_DIR = Path(__file__).parent / "static"


# =============================================================================
# Lifecycle
# =============================================================================

async def on_startup():
    """Initialize deploy API (after kernel DB init)."""
    logger = get_logger()
    
    # Register workspace checker
    if workspace_access is not None:
        workspace_access.set_checker(DeployWorkspaceChecker())
        logger.info("Workspace access checker registered")


async def on_shutdown():
    """Cleanup on shutdown (before kernel DB close)."""
    get_logger().info("Deploy API shutting down")


# =============================================================================
# Build Config
# =============================================================================

def _build_config() -> "ServiceConfig":
    """Build ServiceConfig from settings."""
    settings = get_settings()
    settings.ensure_data_dir()
    
    return ServiceConfig(
        jwt_secret=settings.jwt_secret,
        jwt_expiry_hours=settings.jwt_expiry_hours,
        auth_enabled=settings.auth_enabled,
        allow_self_signup=settings.allow_self_signup,
        redis_url=settings.redis_url,
        redis_key_prefix=settings.redis_key_prefix,
        # Database - kernel manages pool, we provide schema
        database_name=settings.database_path,
        database_type=settings.database_type,
        database_host=settings.database_host,
        database_port=settings.database_port,
        database_user=settings.database_user,
        database_password=settings.database_password,
        cors_origins=settings.cors_origins,
        cors_credentials=True,
        debug=settings.debug,
        log_level="DEBUG" if settings.debug else "INFO",
    )


# =============================================================================
# Create App (with app_kernel)
# =============================================================================

def create_app_with_kernel() -> FastAPI:
    """Create the FastAPI application using app_kernel."""
    settings = get_settings()
    
    app = create_service(
        name="deploy-api",
        version=settings.service_version,
        description=__doc__,
        
        # Routes
        routers=[
            workspaces_router,
            projects_router,
            deployments_router,
        ],
        
        # Background tasks (deployment jobs)
        tasks=TASKS,
        
        # Configuration (includes database)
        config=_build_config(),
        
        # Database schema init (kernel calls this after DB init)
        schema_init=init_schema,
        
        # Auth: kernel auto-creates user store when DB is configured
        # Just need to ensure auth_enabled=True (default) in config
        
        # Lifecycle (after DB init, before DB close)
        on_startup=on_startup,
        on_shutdown=on_shutdown,
    )
    
    return app


# =============================================================================
# Create App
# =============================================================================

def create_app() -> FastAPI:
    """Create the deploy-api application."""
    app = create_app_with_kernel()
    settings = get_settings()
    
    # API info endpoint
    @app.get("/api")
    async def api_info():
        return {
            "service": "deploy-api",
            "version": settings.service_version,
            "docs": "/docs",
        }
    
    # Mount static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    
    # Serve UI at root
    @app.get("/", response_class=HTMLResponse)
    async def serve_ui():
        """Serve the deploy dashboard UI."""
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return HTMLResponse("<h1>Deploy API</h1><p>UI not found. API available at /docs</p>")
    
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "services.deploy_api.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
