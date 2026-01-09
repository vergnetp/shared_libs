"""
Vault service - secrets management implementation.

Priority order:
1. Local files (shared_libs/backend/secrets/ or infra/)
2. Environment variables
3. Infisical vault (remote)
"""

import os
from pathlib import Path
from typing import Optional


# =============================================================================
# Path Discovery
# =============================================================================

def _get_secrets_dir() -> Path:
    """Get the secrets directory (shared_libs/backend/secrets/)."""
    # This file is at shared_libs/backend/vault/service.py
    # So parent.parent = shared_libs/backend/
    backend_dir = Path(__file__).parent.parent
    return backend_dir / "secrets"


def _get_infra_dir() -> Path:
    """Get the infra directory (shared_libs/backend/infra/)."""
    backend_dir = Path(__file__).parent.parent
    return backend_dir / "infra"


# =============================================================================
# Infisical Integration
# =============================================================================

def _fetch_from_infisical(secret_name: str) -> Optional[str]:
    """
    Fetch secret from Infisical vault.
    
    Args:
        secret_name: Name of the secret to fetch
        
    Returns:
        Secret value or None if not found/configured
    """
    token = os.environ.get("INFISICAL_TOKEN")
    project_id = os.environ.get("INFISICAL_PROJECT_ID")
    environment = os.environ.get("INFISICAL_ENV", "prod")
    
    if not token or not project_id:
        return None
    
    try:
        import requests
        
        # Infisical API v3
        url = f"https://app.infisical.com/api/v3/secrets/raw/{secret_name}"
        headers = {"Authorization": f"Bearer {token}"}
        params = {
            "workspaceId": project_id,
            "environment": environment,
        }
        
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("secret", {}).get("secretValue")
        elif resp.status_code == 404:
            return None
        else:
            print(f"[vault] Infisical error for {secret_name}: {resp.status_code}")
            
    except ImportError:
        pass  # requests not installed, skip silently
    except Exception as e:
        print(f"[vault] Infisical fetch failed for {secret_name}: {e}")
    
    return None


# =============================================================================
# File Search
# =============================================================================

def _find_file(filename: str) -> Optional[str]:
    """
    Search for a file in standard locations.
    
    Args:
        filename: File to find (e.g., "certificate.pem")
        
    Returns:
        File contents or None
    """
    search_paths = [
        # Relative to this module
        _get_secrets_dir() / filename,
        _get_infra_dir() / filename,
        # Relative to cwd (for when running from Projects/)
        Path.cwd() / "secrets" / filename,
        Path.cwd() / "infra" / filename,
        Path.cwd() / "shared_libs" / "backend" / "secrets" / filename,
        Path.cwd() / "shared_libs" / "backend" / "infra" / filename,
    ]
    
    for path in search_paths:
        if path.exists():
            print(f"[vault] Found {filename} at: {path}")
            return path.read_text()
    
    return None


# =============================================================================
# Public API
# =============================================================================

def get_secret(
    name: str,
    default: Optional[str] = None,
    filename: Optional[str] = None,
) -> Optional[str]:
    """
    Get a secret value.
    
    Priority order:
    1. Local file (if filename provided)
    2. Environment variable
    3. Infisical vault
    4. Default value
    
    Args:
        name: Secret name (used for env var and Infisical)
        default: Default value if not found anywhere
        filename: Optional filename to check in secrets/infra dirs
        
    Returns:
        Secret value or default
    """
    # 1. Try local file
    if filename:
        value = _find_file(filename)
        if value:
            return value
    
    # 2. Try environment variable
    value = os.environ.get(name)
    if value:
        print(f"[vault] Found {name} in environment variable")
        return value
    
    # 3. Try Infisical
    value = _fetch_from_infisical(name)
    if value:
        print(f"[vault] Found {name} in Infisical")
        return value
    
    # 4. Return default
    return default


def vault_status() -> dict:
    """
    Check vault configuration status.
    
    Returns:
        Dict with status info
    """
    infisical_configured = bool(
        os.environ.get("INFISICAL_TOKEN") and 
        os.environ.get("INFISICAL_PROJECT_ID")
    )
    
    return {
        "infisical_configured": infisical_configured,
        "infisical_env": os.environ.get("INFISICAL_ENV", "prod"),
        "secrets_dir": str(_get_secrets_dir()),
        "infra_dir": str(_get_infra_dir()),
    }
