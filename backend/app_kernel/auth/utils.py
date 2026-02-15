"""
Auth utilities - Token creation/verification and password hashing.

These are low-level primitives used by auth dependencies and services.
"""
import asyncio
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

# Current hashing parameters (OWASP 2024+ recommendation for PBKDF2)
_HASH_ALGORITHM = 'sha512'
_HASH_ITERATIONS = 600000


def hash_password(password: str) -> str:
    """
    Hash a password using PBKDF2-SHA512 with 600k iterations.

    Returns a string in format: salt$iterations$algorithm$hash
    """
    salt = secrets.token_hex(16)

    dk = hashlib.pbkdf2_hmac(
        _HASH_ALGORITHM,
        password.encode('utf-8'),
        salt.encode('utf-8'),
        _HASH_ITERATIONS
    )

    return f"{salt}${_HASH_ITERATIONS}${_HASH_ALGORITHM}${dk.hex()}"


async def hash_password_async(password: str) -> str:
    """Non-blocking hash_password for use in async request handlers."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, hash_password, password)


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a password against its hash.

    Backward-compatible: handles both old (salt$iter$hash with sha256)
    and new (salt$iter$algo$hash) formats.
    """
    try:
        parts = password_hash.split('$')

        if len(parts) == 4:
            # New format: salt$iterations$algorithm$hash
            salt, iterations_str, algorithm, stored_hash = parts
        elif len(parts) == 3:
            # Legacy format: salt$iterations$hash (always sha256)
            salt, iterations_str, stored_hash = parts
            algorithm = 'sha256'
        else:
            return False

        iterations = int(iterations_str)

        dk = hashlib.pbkdf2_hmac(
            algorithm,
            password.encode('utf-8'),
            salt.encode('utf-8'),
            iterations
        )

        return secrets.compare_digest(dk.hex(), stored_hash)
    except (ValueError, AttributeError):
        return False


async def verify_password_async(password: str, password_hash: str) -> bool:
    """Non-blocking verify_password for use in async request handlers."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, verify_password, password, password_hash)


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
