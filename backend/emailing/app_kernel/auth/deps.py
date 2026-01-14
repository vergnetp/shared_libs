"""
Auth dependencies - FastAPI dependency injection for authentication.

These are the dependencies apps inject into their routes to get
authenticated user context.

Usage:
    from app_kernel.auth import get_current_user, require_admin
    
    @app.get("/profile")
    async def get_profile(user: UserIdentity = Depends(get_current_user)):
        return {"id": user.id, "email": user.email}
    
    @app.post("/admin/action")
    async def admin_action(user: UserIdentity = Depends(require_admin)):
        return {"admin_action": "done"}
"""
from typing import Optional, Callable, Awaitable
from functools import wraps

from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .models import UserIdentity, TokenPayload, RequestContext
from .utils import decode_token, AuthError


# Security scheme for Swagger docs
_bearer_scheme = HTTPBearer(auto_error=False)


class AuthDependencies:
    """
    Container for auth dependencies.
    
    Initialized by init_app_kernel() and provides the dependency
    functions used in routes.
    """
    
    def __init__(self, token_secret: str, user_loader: Optional[Callable] = None):
        """
        Initialize auth dependencies.
        
        Args:
            token_secret: Secret for JWT verification
            user_loader: Optional async function to load full user from ID.
                         If not provided, returns UserIdentity from token only.
        """
        self._token_secret = token_secret
        self._user_loader = user_loader
    
    async def get_token_payload(
        self,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
    ) -> Optional[TokenPayload]:
        """
        Extract and verify token from Authorization header.
        
        Returns None if no token provided (for optional auth).
        Raises HTTPException if token is invalid.
        """
        if not credentials:
            return None
        
        try:
            payload = decode_token(credentials.credentials, self._token_secret)
            return payload
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
    
    async def get_current_user(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
    ) -> UserIdentity:
        """
        Get the current authenticated user.
        
        Raises 401 if not authenticated.
        """
        if not credentials:
            raise HTTPException(status_code=401, detail="Not authenticated")
        
        try:
            payload = decode_token(credentials.credentials, self._token_secret)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        
        # If we have a user loader, use it to get full user
        if self._user_loader:
            user = await self._user_loader(payload.sub)
            if not user:
                raise HTTPException(status_code=401, detail="User not found")
            if not user.is_active:
                raise HTTPException(status_code=401, detail="User account disabled")
            return user
        
        # Otherwise, return identity from token
        return UserIdentity(
            id=payload.sub,
            email=payload.email,
            role=payload.role,
            is_active=True
        )
    
    async def get_current_user_optional(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
    ) -> Optional[UserIdentity]:
        """
        Get the current user if authenticated, None otherwise.
        
        Does not raise if not authenticated.
        """
        if not credentials:
            return None
        
        try:
            payload = decode_token(credentials.credentials, self._token_secret)
        except AuthError:
            return None
        
        if self._user_loader:
            user = await self._user_loader(payload.sub)
            if not user or not user.is_active:
                return None
            return user
        
        return UserIdentity(
            id=payload.sub,
            email=payload.email,
            role=payload.role,
            is_active=True
        )
    
    async def require_admin(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
    ) -> UserIdentity:
        """
        Require an authenticated admin user.
        
        Raises 401 if not authenticated, 403 if not admin.
        """
        user = await self.get_current_user(request, credentials)
        
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="Admin access required")
        
        return user
    
    async def get_request_context(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
    ) -> RequestContext:
        """
        Get the full request context including optional user.
        
        Always succeeds - user may be None if not authenticated.
        """
        user = await self.get_current_user_optional(request, credentials)
        
        # Extract request metadata
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")
        
        # Get or generate request ID
        request_id = request.headers.get("x-request-id") or request.state.request_id \
            if hasattr(request.state, 'request_id') else None
        
        ctx = RequestContext(
            user=user,
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        if request_id:
            ctx.request_id = request_id
        
        return ctx


# Module-level instance, initialized by init_app_kernel()
_auth_deps: Optional[AuthDependencies] = None


def init_auth_deps(token_secret: str, user_loader: Optional[Callable] = None):
    """Initialize the auth dependencies. Called by init_app_kernel()."""
    global _auth_deps
    _auth_deps = AuthDependencies(token_secret, user_loader)


def _get_auth_deps() -> AuthDependencies:
    """Get the initialized auth dependencies."""
    if _auth_deps is None:
        raise RuntimeError("Auth dependencies not initialized. Call init_app_kernel() first.")
    return _auth_deps


# Public dependency functions - these are what apps import and use
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
) -> UserIdentity:
    """Get the current authenticated user. Raises 401 if not authenticated."""
    return await _get_auth_deps().get_current_user(request, credentials)


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
) -> Optional[UserIdentity]:
    """Get the current user if authenticated, None otherwise."""
    return await _get_auth_deps().get_current_user_optional(request, credentials)


async def require_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
) -> UserIdentity:
    """Require an authenticated admin user. Raises 401/403 if not authorized."""
    return await _get_auth_deps().require_admin(request, credentials)


async def get_request_context(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme)
) -> RequestContext:
    """Get the full request context including optional user."""
    return await _get_auth_deps().get_request_context(request, credentials)


# Alias for consistency with common naming
require_auth = get_current_user
