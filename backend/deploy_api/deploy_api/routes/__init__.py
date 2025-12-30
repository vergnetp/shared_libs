"""
API Routes for deploy service.
"""
from .workspaces import router as workspaces_router
from .projects import router as projects_router
from .deployments import router as deployments_router

__all__ = [
    "workspaces_router",
    "projects_router",
    "deployments_router",
]
