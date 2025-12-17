"""
Password hashing utilities.

Uses bcrypt by default. Install with: pip install bcrypt
"""
import hashlib
import secrets
from typing import Tuple

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False


def hash_password(password: str) -> str:
    """
    Hash a password securely.
    
    Uses bcrypt if available, falls back to PBKDF2.
    """
    if BCRYPT_AVAILABLE:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    else:
        # Fallback to PBKDF2
        salt = secrets.token_hex(16)
        hash_value = hashlib.pbkdf2_hmac(
            'sha256', 
            password.encode(), 
            salt.encode(), 
            100_000
        ).hex()
        return f"pbkdf2:{salt}:{hash_value}"


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a password against its hash.
    """
    if password_hash.startswith("pbkdf2:"):
        # PBKDF2 fallback format
        _, salt, hash_value = password_hash.split(":")
        computed = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode(),
            salt.encode(),
            100_000
        ).hex()
        return secrets.compare_digest(computed, hash_value)
    elif BCRYPT_AVAILABLE:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    else:
        raise ValueError("Cannot verify bcrypt hash without bcrypt installed")


def generate_token(length: int = 32) -> str:
    """Generate a secure random token."""
    return secrets.token_urlsafe(length)


def hash_token(token: str) -> str:
    """Hash a token for storage (one-way)."""
    return hashlib.sha256(token.encode()).hexdigest()
