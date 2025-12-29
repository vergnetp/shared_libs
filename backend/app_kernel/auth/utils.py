"""
Auth utilities - Token creation/verification and password hashing.

These are low-level primitives used by auth dependencies and services.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Union
import jwt

from .models import UserIdentity, TokenPayload

UTC = timezone.utc


class AuthError(Exception):
    """Raised for authentication/authorization failures."""
    pass


# =============================================================================
# Password Hashing
# =============================================================================

def hash_password(password: str) -> str:
    """
    Hash a password using PBKDF2-SHA256.
    
    Returns a string in format: salt$iterations$hash
    """
    salt = secrets.token_hex(16)
    iterations = 100000
    
    dk = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        iterations
    )
    
    return f"{salt}${iterations}${dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a password against its hash.
    
    Returns True if password matches, False otherwise.
    """
    try:
        salt, iterations_str, stored_hash = password_hash.split('$')
        iterations = int(iterations_str)
        
        dk = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            iterations
        )
        
        return secrets.compare_digest(dk.hex(), stored_hash)
    except (ValueError, AttributeError):
        return False


# =============================================================================
# JWT Token Handling
# =============================================================================

def create_access_token(
    user: UserIdentity,
    secret: str,
    expires_delta: timedelta = timedelta(minutes=15)
) -> str:
    """
    Create an access token for the user.
    
    Args:
        user: User identity to encode in token
        secret: Secret key for signing
        expires_delta: Token expiration time
    
    Returns:
        Encoded JWT token string
    """
    now = datetime.now(UTC)
    expires = now + expires_delta
    
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "type": "access",
        "iat": now,
        "exp": expires
    }
    
    return jwt.encode(payload, secret, algorithm="HS256")


def create_refresh_token(
    user: UserIdentity,
    secret: str,
    expires_delta: timedelta = timedelta(days=30)
) -> str:
    """
    Create a refresh token for the user.
    
    Args:
        user: User identity to encode in token
        secret: Secret key for signing
        expires_delta: Token expiration time
    
    Returns:
        Encoded JWT token string
    """
    now = datetime.now(UTC)
    expires = now + expires_delta
    
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "type": "refresh",
        "iat": now,
        "exp": expires
    }
    
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> TokenPayload:
    """
    Decode and verify a JWT token.
    
    Args:
        token: JWT token string
        secret: Secret key for verification
    
    Returns:
        TokenPayload with decoded claims
    
    Raises:
        AuthError: If token is invalid or expired
    """
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        
        return TokenPayload(
            sub=payload["sub"],
            email=payload.get("email", ""),
            role=payload.get("role", "user"),
            type=payload.get("type", "access"),
            exp=datetime.fromtimestamp(payload["exp"], tz=UTC) if "exp" in payload else None,
            iat=datetime.fromtimestamp(payload["iat"], tz=UTC) if "iat" in payload else None
        )
        
    except jwt.ExpiredSignatureError:
        raise AuthError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid token: {e}")


def verify_token(token: str, secret: str) -> bool:
    """
    Verify a token is valid without decoding.
    
    Returns True if valid, False otherwise.
    """
    try:
        decode_token(token, secret)
        return True
    except AuthError:
        return False
