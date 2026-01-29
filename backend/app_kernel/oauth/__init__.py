"""
OAuth Providers - Google/GitHub login.

Usage:
    # Configure in ServiceConfig
    config = ServiceConfig(
        oauth_providers={
            "google": {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            },
            "github": {
                "client_id": os.environ["GITHUB_CLIENT_ID"],
                "client_secret": os.environ["GITHUB_CLIENT_SECRET"],
            },
        },
        oauth_redirect_url="https://myapp.com/auth/callback",
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
    init_oauth_schema,
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
    "init_oauth_schema",
    # Router
    "create_oauth_router",
]
