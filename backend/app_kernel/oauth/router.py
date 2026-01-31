"""OAuth authentication routes."""

import secrets
from typing import Callable, Dict, List, Optional
from urllib.parse import urlencode
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel


class OAuthAccountResponse(BaseModel):
    id: str
    provider: str
    provider_user_id: str
    email: Optional[str]
    name: Optional[str]
    picture: Optional[str]
    created_at: str


def create_oauth_router(
    get_db_connection: Callable,
    get_current_user: Callable,
    get_current_user_optional: Callable,
    create_user: Callable,
    create_jwt_token: Callable,
    prefix: str = "/auth/oauth",
    redirect_base_url: Optional[str] = None,
    default_redirect: str = "/",
    tags: List[str] = None,
    allow_signup: bool = True,
) -> APIRouter:
    """
    Create OAuth router.
    
    Endpoints:
        GET  /{provider}          - Start OAuth flow
        GET  /{provider}/callback - Handle OAuth callback
        GET  /accounts            - List linked accounts
        POST /link/{provider}     - Link account to current user
        DELETE /{provider}        - Unlink account
    
    Args:
        get_db_connection: Database connection factory
        get_current_user: Auth dependency
        get_current_user_optional: Optional auth dependency
        create_user: Function to create new user (email, name) -> user
        create_jwt_token: Function to create JWT (user) -> token
        redirect_base_url: Base URL for OAuth callbacks
        default_redirect: Where to redirect after login
        allow_signup: Allow creating new users via OAuth
    """
    router = APIRouter(prefix=prefix, tags=tags or ["oauth"])
    
    # State storage (in production, use Redis or encrypted cookies)
    _oauth_states: Dict[str, Dict] = {}
    
    @router.get("/{provider}")
    async def start_oauth(
        provider: str,
        redirect_uri: Optional[str] = Query(None, description="Where to redirect after auth"),
        request: Request = None,
    ):
        """Start OAuth flow - redirects to provider."""
        from .providers import get_provider
        
        oauth_provider = get_provider(provider)
        if not oauth_provider:
            raise HTTPException(400, f"Unknown OAuth provider: {provider}")
        
        # Generate state for CSRF protection
        state = secrets.token_urlsafe(32)
        _oauth_states[state] = {
            "redirect_uri": redirect_uri or default_redirect,
            "provider": provider,
        }
        
        # Build callback URL
        base_url = redirect_base_url or str(request.base_url).rstrip("/")
        callback_url = f"{base_url}{prefix}/{provider}/callback"
        
        # Redirect to provider
        auth_url = oauth_provider.get_authorize_url(callback_url, state)
        return RedirectResponse(auth_url)
    
    @router.get("/{provider}/callback")
    async def oauth_callback(
        provider: str,
        code: str = Query(...),
        state: str = Query(...),
        request: Request = None,
    ):
        """Handle OAuth callback from provider."""
        from .providers import get_provider
        from .stores import get_oauth_account, create_oauth_account, find_user_by_oauth
        
        # Verify state
        state_data = _oauth_states.pop(state, None)
        if not state_data or state_data["provider"] != provider:
            raise HTTPException(400, "Invalid OAuth state")
        
        redirect_uri = state_data["redirect_uri"]
        
        # Get provider
        oauth_provider = get_provider(provider)
        if not oauth_provider:
            raise HTTPException(400, f"Unknown OAuth provider: {provider}")
        
        # Build callback URL (must match what we sent)
        base_url = redirect_base_url or str(request.base_url).rstrip("/")
        callback_url = f"{base_url}{prefix}/{provider}/callback"
        
        try:
            # Exchange code for tokens
            token_data = await oauth_provider.exchange_code(code, callback_url)
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            
            if not access_token:
                raise HTTPException(400, "Failed to get access token")
            
            # Get user info from provider
            oauth_user = await oauth_provider.get_user_info(access_token)
            
        except Exception as e:
            # Redirect with error
            error_params = urlencode({"error": str(e)})
            return RedirectResponse(f"{redirect_uri}?{error_params}")
        
        async with get_db_connection() as db:
            # Check if OAuth account exists
            existing_user_id = await find_user_by_oauth(db, provider, oauth_user.provider_user_id)
            
            if existing_user_id:
                # Existing user - update tokens and login
                await create_oauth_account(
                    db,
                    user_id=existing_user_id,
                    provider=provider,
                    provider_user_id=oauth_user.provider_user_id,
                    email=oauth_user.email,
                    name=oauth_user.name,
                    picture=oauth_user.picture,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    raw_data=oauth_user.raw,
                )
                
                # Get user for JWT
                user = await db.get_entity("kernel_auth_users", existing_user_id)
                if not user:
                    raise HTTPException(400, "User not found")
                
            else:
                # New OAuth user
                if not allow_signup:
                    error_params = urlencode({"error": "Signup not allowed"})
                    return RedirectResponse(f"{redirect_uri}?{error_params}")
                
                # Check if email exists (link to existing account)
                existing_by_email = await db.find_entities(
                    "kernel_auth_users",
                    where_clause="[email] = ?",
                    params=(oauth_user.email,),
                    limit=1,
                )
                
                if existing_by_email:
                    # Link to existing user by email
                    user = existing_by_email[0]
                else:
                    # Create new user
                    user = await create_user(
                        db,
                        email=oauth_user.email,
                        name=oauth_user.name,
                        picture=oauth_user.picture,
                    )
                
                # Create OAuth account link
                await create_oauth_account(
                    db,
                    user_id=user["id"],
                    provider=provider,
                    provider_user_id=oauth_user.provider_user_id,
                    email=oauth_user.email,
                    name=oauth_user.name,
                    picture=oauth_user.picture,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    raw_data=oauth_user.raw,
                )
        
        # Create JWT
        token = create_jwt_token(user)
        
        # Redirect with token
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{separator}token={token}")
    
    @router.get("/accounts", response_model=List[OAuthAccountResponse])
    async def list_oauth_accounts(
        user = Depends(get_current_user),
    ):
        """List OAuth accounts linked to current user."""
        from .stores import get_user_oauth_accounts
        
        async with get_db_connection() as db:
            return await get_user_oauth_accounts(db, user.id)
    
    @router.delete("/{provider}", status_code=204)
    async def unlink_oauth(
        provider: str,
        user = Depends(get_current_user),
    ):
        """Unlink an OAuth account."""
        from .stores import unlink_oauth_account, get_user_oauth_accounts
        
        async with get_db_connection() as db:
            # Check user has other login methods
            accounts = await get_user_oauth_accounts(db, user.id)
            has_password = bool(user.get("password_hash") if isinstance(user, dict) else getattr(user, "password_hash", None))
            
            if len(accounts) <= 1 and not has_password:
                raise HTTPException(400, "Cannot unlink last login method")
            
            success = await unlink_oauth_account(db, user.id, provider)
            
            if not success:
                raise HTTPException(404, f"No {provider} account linked")
    
    return router
