"""API key management routes."""

from typing import List, Optional, Callable
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


class ApiKeyCreate(BaseModel):
    name: str
    scopes: Optional[List[str]] = None
    workspace_id: Optional[str] = None
    expires_in_days: Optional[int] = None


class ApiKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: List[str]
    workspace_id: Optional[str]
    expires_at: Optional[str]
    last_used_at: Optional[str]
    revoked_at: Optional[str]
    created_at: str


class ApiKeyCreated(BaseModel):
    id: str
    key: str  # Only returned on creation!
    name: str
    scopes: List[str]
    expires_at: Optional[str]
    created_at: str


def create_api_keys_router(
    get_current_user: Callable,
    get_db_connection: Callable,
    prefix: str = "/api-keys",
    tags: List[str] = None,
) -> APIRouter:
    """
    Create API keys management router.
    
    Endpoints:
        POST   /api-keys           - Create new key
        GET    /api-keys           - List user's keys
        GET    /api-keys/{id}      - Get key details
        DELETE /api-keys/{id}      - Revoke key
    """
    router = APIRouter(prefix=prefix, tags=tags or ["api-keys"])
    
    @router.post("", response_model=ApiKeyCreated, status_code=201)
    async def create_key(
        data: ApiKeyCreate,
        user = Depends(get_current_user),
    ):
        """Create a new API key. The key value is only returned once!"""
        from .stores import create_api_key
        
        async with get_db_connection() as db:
            result = await create_api_key(
                db,
                user_id=user.id,
                workspace_id=data.workspace_id,
                name=data.name,
                scopes=data.scopes,
                expires_in_days=data.expires_in_days,
            )
        
        return result
    
    @router.get("", response_model=List[ApiKeyResponse])
    async def list_keys(
        workspace_id: Optional[str] = None,
        include_revoked: bool = False,
        user = Depends(get_current_user),
    ):
        """List all API keys for the current user."""
        from .stores import list_api_keys
        
        async with get_db_connection() as db:
            return await list_api_keys(
                db,
                user_id=user.id,
                workspace_id=workspace_id,
                include_revoked=include_revoked,
            )
    
    @router.get("/{key_id}", response_model=ApiKeyResponse)
    async def get_key(
        key_id: str,
        user = Depends(get_current_user),
    ):
        """Get API key details."""
        from .stores import get_api_key
        
        async with get_db_connection() as db:
            key = await get_api_key(db, key_id, user.id)
        
        if not key:
            raise HTTPException(404, "API key not found")
        
        return key
    
    @router.delete("/{key_id}", status_code=204)
    async def revoke_key(
        key_id: str,
        user = Depends(get_current_user),
    ):
        """Revoke an API key."""
        from .stores import revoke_api_key
        
        async with get_db_connection() as db:
            success = await revoke_api_key(db, key_id, user.id)
        
        if not success:
            raise HTTPException(404, "API key not found")
    
    return router
