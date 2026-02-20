"""
Task cancellation router.

Usage:
    from app_kernel.tasks import create_tasks_router
    
    # Auto-mounted by kernel, or manually:
    app.include_router(create_tasks_router())
"""

from fastapi import APIRouter, HTTPException, Depends, Request

from . import cancel


def create_tasks_router(auth_dependency=None) -> APIRouter:
    """Create router with task cancellation endpoint.
    
    Args:
        auth_dependency: Optional FastAPI dependency for auth. 
            If None, endpoint is unprotected.
    """
    router = APIRouter(tags=["tasks"])
    
    deps = [Depends(auth_dependency)] if auth_dependency else []
    
    @router.post(
        "/tasks/{task_id}/cancel", 
        summary="Cancel a running task",
        dependencies=deps,
    )
    async def cancel_task(task_id: str, request: Request):
        """Cancel a running task by its task_id (emitted as SSE event at start).
        
        Any X-*-Token headers (e.g. X-DO-Token, X-CF-Token) are forwarded
        to cleanup callbacks so they can authenticate against external APIs.
        """
        # Extract token headers for cleanup callbacks
        tokens = {
            k: v for k, v in request.headers.items()
            if k.lower().startswith('x-') and k.lower().endswith('-token')
        }
        
        if cancel.trigger(task_id, tokens=tokens):
            return {"status": "cancelling", "task_id": task_id}
        else:
            raise HTTPException(400, "Task is not currently running on this server")
    
    @router.get(
        "/tasks/{task_id}/status",
        summary="Check if a task is running",
        dependencies=deps,
    )
    async def task_status(task_id: str):
        """Check if a task is currently active."""
        return {
            "task_id": task_id,
            "active": cancel.is_active(task_id),
            "cancelled": cancel.is_cancelled(task_id),
        }
    
    return router
