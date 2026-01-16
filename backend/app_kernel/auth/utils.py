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
    user_id: str = None,
    role: str = "user",
    email: str = "",
    secret: str = "",
    expires_minutes: int = 15,
    *,
    user: "UserIdentity" = None,
    expires_delta: timedelta = None,
) -> str:
    """
    Create an access token.
    
    Can be called two ways:
    1. create_access_token(user_id="123", role="user", email="user@example.com", secret="...", expires_minutes=15)
    2. create_access_token(user=user_identity, secret="...", expires_delta=timedelta(...))
    
    Args:
        user_id: User ID to encode
        role: User role
        email: User email
        secret: Secret key for signing
        expires_minutes: Token expiration in minutes
        user: UserIdentity object (alternative to user_id/role/email)
        expires_delta: Token expiration as timedelta (alternative to expires_minutes)
    
    Returns:
        Encoded JWT token string
    """
    now = datetime.now(UTC)
    
    # Handle both calling conventions
    if user is not None:
        _user_id = user.id
        _role = user.role
        _email = user.email
    else:
        _user_id = user_id
        _role = role
        _email = email
    
    if expires_delta is not None:
        expires = now + expires_delta
    else:
        expires = now + timedelta(minutes=expires_minutes)
    
    payload = {
        "sub": _user_id,
        "email": _email,
        "role": _role,
        "type": "access",
        "iat": now,
        "exp": expires
    }
    
    return jwt.encode(payload, secret, algorithm="HS256")


def create_refresh_token(
    user_id: str = None,
    secret: str = "",
    expires_days: int = 30,
    *,
    user: "UserIdentity" = None,
    expires_delta: timedelta = None,
) -> str:
    """
    Create a refresh token.
    
    Can be called two ways:
    1. create_refresh_token(user_id="123", secret="...", expires_days=30)
    2. create_refresh_token(user=user_identity, secret="...", expires_delta=timedelta(...))
    
    Args:
        user_id: User ID to encode
        secret: Secret key for signing
        expires_days: Token expiration in days
        user: UserIdentity object (alternative to user_id)
        expires_delta: Token expiration as timedelta (alternative to expires_days)
    
    Returns:
        Encoded JWT token string
    """
    now = datetime.now(UTC)
    
    # Handle both calling conventions
    if user is not None:
        _user_id = user.id
    else:
        _user_id = user_id
    
    if expires_delta is not None:
        expires = now + expires_delta
    else:
        expires = now + timedelta(days=expires_days)
    
    payload = {
        "sub": _user_id,
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
