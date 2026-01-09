"""
Vault - Centralized secrets management for all services.

Supports (in order of priority):
1. Local files (for development)
2. Environment variables
3. Infisical vault (for production)

Usage:
    from shared_libs.backend.vault import get_secret
    
    # Fetch any secret
    api_key = get_secret("MY_API_KEY")
    
    # With file fallback
    cert = get_secret("CERTIFICATE_PEM", filename="certificate.pem")

Configuration (env vars for Infisical):
    INFISICAL_TOKEN - Service token from Infisical
    INFISICAL_PROJECT_ID - Project/workspace ID
    INFISICAL_ENV - Environment (default: prod)
"""

from .service import (
    get_secret,
    vault_status,
)

__all__ = [
    "get_secret",
    "vault_status",
]
