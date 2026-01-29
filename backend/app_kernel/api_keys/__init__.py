"""
API Keys - Service-to-service authentication.

Usage:
    # Create key (returns plaintext only once)
    key_data = await create_api_key(db, user_id, workspace_id,
        name="CI/CD Pipeline",
        scopes=["deployments:write", "services:read"],
        expires_in_days=90,
    )
    # key_data = {"id": "...", "key": "sk_live_a1b2c3...", "name": "CI/CD Pipeline", ...}
    
    # Use in requests:
    # Authorization: Bearer sk_live_a1b2c3...
    
    # In routes - accept API key OR JWT
    @router.post("/deployments")
    async def deploy(auth=Depends(get_auth)):
        # auth.type = "api_key" or "user"
        # auth.user_id, auth.workspace_id, auth.scopes
    
    # List keys (never returns plaintext)
    keys = await list_api_keys(db, user_id)
    
    # Revoke
    await revoke_api_key(db, key_id, user_id)
"""

from .stores import (
    create_api_key,
    verify_api_key,
    list_api_keys,
    get_api_key,
    revoke_api_key,
    init_api_keys_schema,
)
from .deps import get_api_key_auth, get_auth, ApiKeyAuth
from .router import create_api_keys_router

__all__ = [
    # Stores
    "create_api_key",
    "verify_api_key", 
    "list_api_keys",
    "get_api_key",
    "revoke_api_key",
    "init_api_keys_schema",
    # Deps
    "get_api_key_auth",
    "get_auth",
    "ApiKeyAuth",
    # Router
    "create_api_keys_router",
]
