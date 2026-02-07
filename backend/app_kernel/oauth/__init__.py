"""
OAuth Providers - Google/GitHub login.

Usage:
    # Configure in create_service
    app = create_service(
        name="my-app",
        oauth_google=("client_id", "client_secret"),
        oauth_github=("client_id", "client_secret"),
        ...
    )
    
    # Auto-mounted routes:
    # GET  /auth/oauth/{provider}          - Start OAuth flow
    # GET  /auth/oauth/{provider}/callback - Handle callback
    # POST /auth/oauth/link                - Link OAuth to account
    # DELETE /auth/oauth/{provider}        - Unlink OAuth
    
    # Frontend redirects user to:
    # /api/v1/auth/oauth/google?redirect_uri=https://app.com/dashboard
"""

from .providers import (
    OAuthProvider,
    GoogleProvider,
    GitHubProvider,
    get_provider,
    register_provider,
    configure_providers,
)
from .stores import (
    create_oauth_account,
    get_oauth_account,
    get_user_oauth_accounts,
    link_oauth_account,
    unlink_oauth_account,
)
from .router import create_oauth_router

__all__ = [
    # Providers
    "OAuthProvider",
    "GoogleProvider", 
    "GitHubProvider",
    "get_provider",
    "register_provider",
    "configure_providers",
    # Stores
    "create_oauth_account",
    "get_oauth_account",
    "get_user_oauth_accounts",
    "link_oauth_account",
    "unlink_oauth_account",
    # Router
    "create_oauth_router",
]
