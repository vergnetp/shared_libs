"""
Application entry point.

This file is generated ONCE - safe to customize after creation.
"""

from backend.app_kernel import create_service, ServiceConfig
from ._gen import init_schema, gen_router
from .src.routes import router as custom_router
from .config import settings


def _build_config() -> ServiceConfig:
    """Build kernel configuration from settings."""
    # Ensure data directory exists
    settings.ensure_data_dir()
    
    return ServiceConfig(
        # Auth
        jwt_secret=settings.jwt_secret,
        
        # Database
        database_name=settings.database_path,
        database_type=settings.database_type,
        
        # Redis
        redis_url=settings.redis_url,
    )


app = create_service(
    name="example_app",
    config=_build_config(),
    schema_init=init_schema,
    routers=[gen_router, custom_router],
)
