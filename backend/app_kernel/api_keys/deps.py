"""FastAPI dependencies for API key authentication."""

from dataclasses import dataclass
from typing import Optional, List, Union, Callable
from fastapi import Depends, HTTPException, Request


@dataclass
class ApiKeyAuth:
    """API key authentication result."""
    type: str = "api_key"
    key_id: str = ""
    user_id: str = ""
    workspace_id: Optional[str] = None
    scopes: List[str] = None
    
    def __post_init__(self):
        if self.scopes is None:
            self.scopes = []
    
    def has_scope(self, scope: str) -> bool:
        """Check if key has a specific scope."""
        if not self.scopes:
            return True  # No scopes = full access
        
        # Check exact match
        if scope in self.scopes:
            return True
        
        # Check wildcard (e.g., "deployments:*" matches "deployments:read")
        resource = scope.split(":")[0]
        if f"{resource}:*" in self.scopes:
            return True
        
        # Check global wildcard
        if "*" in self.scopes:
            return True
        
        return False


def create_api_key_auth(get_db_connection: Callable):
    """
    Create API key auth dependency with database connection.
    
    Usage:
        get_api_key_auth = create_api_key_auth(get_db_connection)
        
        @router.get("/data")
        async def get_data(auth: ApiKeyAuth = Depends(get_api_key_auth)):
            ...
    """
    async def get_api_key_auth(request: Request) -> ApiKeyAuth:
        from .stores import verify_api_key
        
        # Get Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(401, "Missing Authorization header")
        
        # Parse Bearer token
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(401, "Invalid Authorization header format")
        
        token = parts[1]
        
        # Check if it looks like an API key (has prefix)
        if not token.startswith(("sk_", "pk_", "key_")):
            raise HTTPException(401, "Invalid API key format")
        
        # Verify key
        async with get_db_connection() as db:
            key_data = await verify_api_key(db, token)
        
        if not key_data:
            raise HTTPException(401, "Invalid or expired API key")
        
        return ApiKeyAuth(
            key_id=key_data["id"],
            user_id=key_data["user_id"],
            workspace_id=key_data.get("workspace_id"),
            scopes=key_data.get("scopes", []),
        )
    
    return get_api_key_auth


def create_combined_auth(get_db_connection: Callable, get_current_user: Callable):
    """
    Create auth dependency that accepts both API key and JWT.
    
    Usage:
        get_auth = create_combined_auth(get_db_connection, get_current_user)
        
        @router.post("/deployments")
        async def deploy(auth = Depends(get_auth)):
            if auth.type == "api_key":
                # Service-to-service
            else:
                # User request
    """
    @dataclass
    class CombinedAuth:
        type: str  # "api_key" or "user"
        user_id: str
        workspace_id: Optional[str] = None
        scopes: List[str] = None
        email: Optional[str] = None
        role: Optional[str] = None
        
        def __post_init__(self):
            if self.scopes is None:
                self.scopes = []
        
        def has_scope(self, scope: str) -> bool:
            """Check if has a specific scope (API keys) or is admin (users)."""
            if self.type == "user":
                return self.role == "admin" or True  # Users have full access
            
            if not self.scopes:
                return True
            
            resource = scope.split(":")[0]
            return (
                scope in self.scopes or
                f"{resource}:*" in self.scopes or
                "*" in self.scopes
            )
    
    async def get_auth(request: Request) -> CombinedAuth:
        from .stores import verify_api_key
        
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise HTTPException(401, "Missing Authorization header")
        
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise HTTPException(401, "Invalid Authorization header format")
        
        token = parts[1]
        
        # Check if API key (has prefix)
        if token.startswith(("sk_", "pk_", "key_")):
            async with get_db_connection() as db:
                key_data = await verify_api_key(db, token)
            
            if not key_data:
                raise HTTPException(401, "Invalid or expired API key")
            
            return CombinedAuth(
                type="api_key",
                user_id=key_data["user_id"],
                workspace_id=key_data.get("workspace_id"),
                scopes=key_data.get("scopes", []),
            )
        
        # Otherwise treat as JWT - use existing auth
        try:
            user = await get_current_user(request)
            return CombinedAuth(
                type="user",
                user_id=user.id,
                workspace_id=getattr(user, "workspace_id", None),
                email=getattr(user, "email", None),
                role=getattr(user, "role", None),
            )
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(401, f"Authentication failed: {e}")
    
    return get_auth


def require_scope(scope: str):
    """
    Dependency that checks for a specific scope.
    
    Usage:
        @router.delete("/deployments/{id}")
        async def delete(
            id: str,
            auth = Depends(get_auth),
            _ = Depends(require_scope("deployments:delete")),
        ):
            ...
    """
    def checker(auth = Depends(lambda: None)):  # Placeholder, needs actual auth
        if not hasattr(auth, "has_scope"):
            raise HTTPException(403, "Cannot check scope")
        if not auth.has_scope(scope):
            raise HTTPException(403, f"Missing required scope: {scope}")
        return True
    
    return checker


# Module-level placeholders (set by app_kernel init)
get_api_key_auth = None
get_auth = None
