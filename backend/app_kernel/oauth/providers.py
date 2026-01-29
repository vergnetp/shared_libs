"""OAuth provider implementations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any, Optional
from urllib.parse import urlencode
import httpx


@dataclass
class OAuthUser:
    """User info from OAuth provider."""
    provider: str
    provider_user_id: str
    email: str
    name: Optional[str] = None
    picture: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None


class OAuthProvider(ABC):
    """Base OAuth provider."""
    
    name: str = ""
    authorize_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    scopes: list = []
    
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
    
    def get_authorize_url(self, redirect_uri: str, state: str) -> str:
        """Get URL to redirect user to for authorization."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
        }
        params.update(self._extra_authorize_params())
        return f"{self.authorize_url}?{urlencode(params)}"
    
    def _extra_authorize_params(self) -> Dict[str, str]:
        """Override to add provider-specific params."""
        return {}
    
    async def exchange_code(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """Exchange authorization code for tokens."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            return response.json()
    
    @abstractmethod
    async def get_user_info(self, access_token: str) -> OAuthUser:
        """Get user info from provider."""
        pass


class GoogleProvider(OAuthProvider):
    """Google OAuth provider."""
    
    name = "google"
    authorize_url = "https://accounts.google.com/o/oauth2/v2/auth"
    token_url = "https://oauth2.googleapis.com/token"
    userinfo_url = "https://www.googleapis.com/oauth2/v2/userinfo"
    scopes = ["openid", "email", "profile"]
    
    def _extra_authorize_params(self) -> Dict[str, str]:
        return {"access_type": "offline", "prompt": "consent"}
    
    async def get_user_info(self, access_token: str) -> OAuthUser:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            data = response.json()
        
        return OAuthUser(
            provider="google",
            provider_user_id=data["id"],
            email=data["email"],
            name=data.get("name"),
            picture=data.get("picture"),
            access_token=access_token,
            raw=data,
        )


class GitHubProvider(OAuthProvider):
    """GitHub OAuth provider."""
    
    name = "github"
    authorize_url = "https://github.com/login/oauth/authorize"
    token_url = "https://github.com/login/oauth/access_token"
    userinfo_url = "https://api.github.com/user"
    scopes = ["read:user", "user:email"]
    
    async def get_user_info(self, access_token: str) -> OAuthUser:
        async with httpx.AsyncClient() as client:
            # Get user profile
            response = await client.get(
                self.userinfo_url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
            )
            response.raise_for_status()
            data = response.json()
            
            # GitHub might not include email in profile, need separate call
            email = data.get("email")
            if not email:
                email_response = await client.get(
                    "https://api.github.com/user/emails",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                if email_response.status_code == 200:
                    emails = email_response.json()
                    # Find primary email
                    for e in emails:
                        if e.get("primary"):
                            email = e.get("email")
                            break
                    if not email and emails:
                        email = emails[0].get("email")
        
        return OAuthUser(
            provider="github",
            provider_user_id=str(data["id"]),
            email=email or f"{data['login']}@github.local",
            name=data.get("name") or data.get("login"),
            picture=data.get("avatar_url"),
            access_token=access_token,
            raw=data,
        )


# Provider registry
_providers: Dict[str, OAuthProvider] = {}


def register_provider(provider: OAuthProvider) -> None:
    """Register an OAuth provider."""
    _providers[provider.name] = provider


def get_provider(name: str) -> Optional[OAuthProvider]:
    """Get a registered OAuth provider."""
    return _providers.get(name)


def configure_providers(providers: Dict[str, Dict[str, str]]) -> None:
    """
    Configure OAuth providers from config dict.
    
    Args:
        providers: Dict of provider_name -> {client_id, client_secret}
    """
    provider_classes = {
        "google": GoogleProvider,
        "github": GitHubProvider,
    }
    
    for name, config in providers.items():
        if name in provider_classes:
            provider = provider_classes[name](
                client_id=config["client_id"],
                client_secret=config["client_secret"],
            )
            register_provider(provider)
