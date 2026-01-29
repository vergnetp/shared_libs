"""Feature flags admin routes."""

from typing import Dict, List, Optional, Any, Callable
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel


class FlagCreate(BaseModel):
    name: str
    description: Optional[str] = None
    enabled: bool = True
    rollout_percent: int = 100
    workspaces: Optional[List[str]] = None
    users: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class FlagResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    enabled: bool
    rollout_percent: int
    allowed_workspaces: List[str]
    allowed_users: List[str]
    metadata: Dict[str, Any]
    created_at: Optional[str]
    updated_at: Optional[str]


class FlagCheckResponse(BaseModel):
    flag: str
    enabled: bool


def create_flags_router(
    get_current_user: Callable,
    get_db_connection: Callable,
    prefix: str = "/flags",
    tags: List[str] = None,
    is_admin: Optional[Callable] = None,
) -> APIRouter:
    """
    Create feature flags admin router.
    
    Endpoints:
        GET    /flags           - List all flags (admin)
        POST   /flags           - Create/update flag (admin)
        GET    /flags/{name}    - Get flag details (admin)
        DELETE /flags/{name}    - Delete flag (admin)
        GET    /flags/{name}/check - Check if flag enabled for user
    """
    router = APIRouter(prefix=prefix, tags=tags or ["feature-flags"])
    
    def _check_admin(user):
        if is_admin:
            return is_admin(user)
        role = getattr(user, "role", None)
        return role == "admin"
    
    def require_admin(user = Depends(get_current_user)):
        if not _check_admin(user):
            raise HTTPException(403, "Admin access required")
        return user
    
    @router.get("", response_model=List[FlagResponse])
    async def list_all_flags(
        user = Depends(require_admin),
    ):
        """List all feature flags."""
        from .stores import list_flags
        
        async with get_db_connection() as db:
            return await list_flags(db)
    
    @router.post("", response_model=FlagResponse)
    async def create_or_update_flag(
        data: FlagCreate,
        user = Depends(require_admin),
    ):
        """Create or update a feature flag."""
        from .stores import set_flag
        
        async with get_db_connection() as db:
            return await set_flag(
                db,
                name=data.name,
                enabled=data.enabled,
                description=data.description,
                rollout_percent=data.rollout_percent,
                workspaces=data.workspaces,
                users=data.users,
                metadata=data.metadata,
            )
    
    @router.get("/{name}", response_model=FlagResponse)
    async def get_flag_details(
        name: str,
        user = Depends(require_admin),
    ):
        """Get feature flag details."""
        from .stores import get_flag
        
        async with get_db_connection() as db:
            flag = await get_flag(db, name)
        
        if not flag:
            raise HTTPException(404, f"Flag not found: {name}")
        
        return flag
    
    @router.delete("/{name}", status_code=204)
    async def remove_flag(
        name: str,
        user = Depends(require_admin),
    ):
        """Delete a feature flag."""
        from .stores import delete_flag
        
        async with get_db_connection() as db:
            success = await delete_flag(db, name)
        
        if not success:
            raise HTTPException(404, f"Flag not found: {name}")
    
    @router.get("/{name}/check", response_model=FlagCheckResponse)
    async def check_flag(
        name: str,
        workspace_id: Optional[str] = None,
        user = Depends(get_current_user),
    ):
        """Check if a flag is enabled for current user."""
        from .stores import flag_enabled
        
        ws_id = workspace_id or getattr(user, "workspace_id", None)
        
        async with get_db_connection() as db:
            enabled = await flag_enabled(
                db,
                name,
                user_id=user.id,
                workspace_id=ws_id,
            )
        
        return FlagCheckResponse(flag=name, enabled=enabled)
    
    return router
