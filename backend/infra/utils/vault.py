"""
Vault - Re-exported from shared_libs.backend.vault.

No fallback - if vault module is not installed, fail fast.
"""

from shared_libs.backend.vault import get_secret, vault_status

def get_origin_cert():
    """Get Cloudflare Origin Certificate."""
    return get_secret("CERTIFICATE_PEM", filename="certificate.pem")

def get_origin_key():
    """Get Cloudflare Origin Certificate private key."""
    return get_secret("CERTIFICATE_KEY", filename="certificate.key")

__all__ = [
    "get_secret",
    "get_origin_cert",
    "get_origin_key",
    "vault_status",
]
