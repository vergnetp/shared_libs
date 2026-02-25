"""Action replay API routes."""

from typing import Callable, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..db.session import raw_db_context


class ActionReplayRequest(BaseModel):
    error_message: str | None = None
    error_source: str | None = None
    url: str | None = None
    user_agent: str | None = None
    replay_log: str | None = None


class ActionReplaySummary(BaseModel):
    model_config = {"extra": "ignore"}
    id: str
    user_id: str | None = None
    workspace_id: str | None = None
    error_message: str | None = None
    error_source: str | None = None
    url: str | None = None
    resolved: bool = False
    timestamp: str | None = None
    created_at: str | None = None


def create_action_replay_router(
    get_current_user: Callable,
    get_current_user_optional: Callable = None,
    prefix: str = "",
    tags: List[str] = None,
    is_admin: Callable = None,
) -> APIRouter:
    """
    Create action replay router.
    
    Endpoints:
        POST /action-replay              - Save replay (auth optional)
        GET  /action-replays             - List replays (admin)
        GET  /action-replays/{id}        - Get full replay (admin)
        PATCH /action-replays/{id}/resolve - Mark resolved (admin)
    
    Args:
        get_current_user: Auth dependency (required for admin routes)
        get_current_user_optional: Optional auth dependency for save route.
            If not provided, save route has no auth (always works).
        prefix: Route prefix
        tags: OpenAPI tags
        is_admin: Callable(user) -> bool. Defaults to role == "admin".
    """
    router = APIRouter(prefix=prefix, tags=tags or ["action-replay"])
    
    def _check_admin(user):
        if is_admin:
            return is_admin(user)
        role = getattr(user, "role", None)
        return role == "admin"
    
    # ── Save replay (auth optional — errors can happen before login) ──
    
    @router.post("/action-replay")
    async def save_action_replay(req: ActionReplayRequest, request: Request):
        """Auto-called by frontend when an error occurs."""
        from .stores import save_replay
        
        # Try to extract user if auth is available
        user_id = None
        workspace_id = None
        if get_current_user_optional:
            try:
                user = await get_current_user_optional(request)
                if user:
                    user_id = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
                    workspace_id = getattr(user, "workspace_id", None) or user_id
            except Exception:
                pass
        
        async with raw_db_context() as db:
            replay_id = await save_replay(
                db,
                error_message=req.error_message,
                error_source=req.error_source,
                url=req.url,
                user_agent=req.user_agent,
                replay_log=req.replay_log,
                user_id=user_id,
                workspace_id=workspace_id,
            )
        
        return {"id": replay_id, "saved": True}
    
    # ── Admin: list replays ──
    
    @router.get("/action-replays")
    async def list_action_replays(
        resolved: bool | None = None,
        since: str | None = Query(None, description="From timestamp (ISO format)"),
        until: str | None = Query(None, description="To timestamp (ISO format)"),
        limit: int = Query(50, le=200),
        offset: int = Query(0, ge=0),
        user=Depends(get_current_user),
    ):
        """List recent action replays. Admin only."""
        from .stores import list_replays, count_replays
        
        if not _check_admin(user):
            raise HTTPException(403, "Admin access required")
        
        async with raw_db_context() as db:
            replays = await list_replays(
                db, resolved=resolved, since=since, until=until,
                limit=limit, offset=offset,
            )
            total = await count_replays(db, resolved=resolved)
        
        return {"replays": replays, "total": total}
    
    # ── Admin: get full replay ──
    
    @router.get("/action-replays/{replay_id}")
    async def get_action_replay(
        replay_id: str,
        user=Depends(get_current_user),
    ):
        """Get full action replay including log entries. Admin only."""
        from .stores import get_replay
        
        if not _check_admin(user):
            raise HTTPException(403, "Admin access required")
        
        async with raw_db_context() as db:
            replay = await get_replay(db, replay_id)
        
        if not replay:
            raise HTTPException(404, "Replay not found")
        
        return replay
    
    # ── Admin: resolve ──
    
    @router.patch("/action-replays/{replay_id}/resolve")
    async def resolve_action_replay(
        replay_id: str,
        user=Depends(get_current_user),
    ):
        """Mark an action replay as resolved. Admin only."""
        from .stores import resolve_replay
        
        if not _check_admin(user):
            raise HTTPException(403, "Admin access required")
        
        async with raw_db_context() as db:
            await resolve_replay(db, replay_id)
        
        return {"resolved": True}
    
    return router
