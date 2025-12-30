"""
API Routes.

App-specific routes only. Infrastructure routes (health, metrics, jobs)
are provided by app_kernel.
"""

from .agents import router as agents_router
from .threads import router as threads_router
from .chat import router as chat_router
from .documents import router as documents_router
from .analytics import router as analytics_router
from .workspaces import router as workspaces_router

__all__ = [
    "agents_router",
    "threads_router",
    "chat_router",
    "documents_router",
    "analytics_router",
    "workspaces_router",
]
