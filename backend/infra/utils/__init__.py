"""Utility functions."""

from .naming import (
    DeploymentNaming,
    DONaming,
    sanitize_for_dns,
    sanitize_for_tag,
    sanitize_for_docker,
    generate_friendly_name,
)

# Vault is optional - may not be installed
try:
    from .vault import (
        get_secret,
        get_origin_cert,
        get_origin_key,
        vault_status,
    )
except ImportError:
    get_secret = None
    get_origin_cert = None
    get_origin_key = None
    vault_status = None

from .encryption import Encryption

__all__ = [
    "DeploymentNaming",
    "DONaming",
    "sanitize_for_dns",
    "sanitize_for_tag",
    "sanitize_for_docker",
    "generate_friendly_name",
    # Vault (optional)
    "get_secret",
    "get_origin_cert",
    "get_origin_key",
    "vault_status",
    # Encryption
    "Encryption",
]
