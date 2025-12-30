"""
Job management routes.

Provides generic job status/management endpoints:
- GET /jobs/{job_id} - Get job status
- GET /jobs - List jobs  
- POST /jobs/{job_id}/cancel - Cancel job

These are infrastructure endpoints, not app-specific.
"""

import json
from typing import Optional, Callable

from fastapi import APIRouter, Depends, HTTPException


def _parse_json(val, default=None):
    """Parse JSON string or return as-is if already parsed."""
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


def create_jobs_router(
    get_db: Callable,
    get_job_client: Callable = None,
    prefix: str = "/jobs",
    tags: list = None,
) -> APIRouter:
    """
    Create job management router.
    
    Args:
        get_db: Dependency that yields database connection
        get_job_client: Optional function to get job client (for Redis-backed jobs)
        prefix: URL prefix for routes
        tags: OpenAPI tags
        
    Returns:
        Configured APIRouter
    """
    router = APIRouter(prefix=prefix, tags=tags or ["jobs"])
    
    @router.get("/{job_id}")
    async def get_job_status(
        job_id: str,
        db=Depends(get_db),
    ):
        """
        Get job status for async processing.
        
        Poll this endpoint to check if an async job is complete.
        
        Returns:
            - status: queued | running | succeeded | failed | cancelled
            - result: Job result (if succeeded)
            - error: Error message (if failed)
        """
        # Try job client first (Redis-backed)
        if get_job_client:
            try:
                client = get_job_client()
                if hasattr(client, 'get_status'):
                    status = await client.get_status(job_id)
                    if status:
                        return status
            except (RuntimeError, Exception):
                pass
        
        # Fall back to direct DB query
        job = await db.get_entity("jobs", job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        
        return {
            "job_id": job_id,
            "status": job.get("status", "unknown"),
            "task": job.get("task"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "result": _parse_json(job.get("result")),
            "error": job.get("error"),
            "attempts": job.get("attempts", 0),
        }
    
    @router.get("")
    async def list_jobs(
        status: Optional[str] = None,
        task: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
        db=Depends(get_db),
    ):
        """
        List recent jobs.
        
        Args:
            status: Filter by status (queued, running, succeeded, failed, cancelled)
            task: Filter by task type
            user_id: Filter by user
            limit: Max jobs to return
        """
        conditions = []
        params = []
        
        if status:
            conditions.append("[status] = ?")
            params.append(status)
        
        if task:
            conditions.append("[task] = ?")
            params.append(task)
        
        if user_id:
            conditions.append("[user_id] = ?")
            params.append(user_id)
        
        jobs = await db.find_entities(
            "jobs",
            where_clause=" AND ".join(conditions) if conditions else None,
            params=tuple(params) if params else None,
            order_by="created_at DESC",
            limit=limit,
        )
        
        return {
            "jobs": [
                {
                    "job_id": j.get("id"),
                    "task": j.get("task"),
                    "status": j.get("status"),
                    "user_id": j.get("user_id"),
                    "created_at": j.get("created_at"),
                    "started_at": j.get("started_at"),
                    "completed_at": j.get("completed_at"),
                    "error": j.get("error"),
                }
                for j in (jobs or [])
            ],
            "total": len(jobs) if jobs else 0,
        }
    
    @router.post("/{job_id}/cancel")
    async def cancel_job(
        job_id: str,
        db=Depends(get_db),
    ):
        """
        Cancel a pending job.
        
        Only works for jobs that haven't started yet.
        """
        # Try job client first
        if get_job_client:
            try:
                client = get_job_client()
                if hasattr(client, 'cancel'):
                    result = await client.cancel(job_id)
                    return {"job_id": job_id, "cancelled": result}
            except (RuntimeError, Exception):
                pass
        
        # Direct DB update
        job = await db.get_entity("jobs", job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        
        if job.get("status") not in ("pending", "queued"):
            return {"job_id": job_id, "cancelled": False, "reason": "Job already started"}
        
        job["status"] = "cancelled"
        await db.save_entity("jobs", job)
        return {"job_id": job_id, "cancelled": True}
    
    return router
