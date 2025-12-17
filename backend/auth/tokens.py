"""
JWT token utilities.

Install with: pip install PyJWT
"""
from datetime import datetime, timedelta
from typing import Optional, Any

try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False

from .models import User


class TokenError(Exception):
    """Raised when token operations fail."""
    pass


def create_jwt(
    user: User,
    secret: str,
    expires_in: timedelta = timedelta(hours=24),
    algorithm: str = "HS256",
    extra_claims: Optional[dict] = None
) -> str:
    """
    Create a JWT token for a user.
    
    Args:
        user: The user to create token for
        secret: Secret key for signing
        expires_in: Token lifetime (default 24 hours)
        algorithm: JWT algorithm (default HS256)
        extra_claims: Additional claims to include
    
    Returns:
        Signed JWT string
    """
    if not JWT_AVAILABLE:
        raise TokenError("PyJWT not installed. Run: pip install PyJWT")
    
    now = datetime.utcnow()
    payload = {
        "sub": user.id,
        "email": user.email,
        "iat": now,
        "exp": now + expires_in,
    }
    
    if extra_claims:
        payload.update(extra_claims)
    
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_jwt(
    token: str,
    secret: str,
    algorithms: list[str] = None
) -> dict[str, Any]:
    """
    Decode and verify a JWT token.
    
    Args:
        token: JWT string
        secret: Secret key for verification
        algorithms: Allowed algorithms (default ["HS256"])
    
    Returns:
        Decoded payload dict
    
    Raises:
        TokenError: If token is invalid or expired
    """
    if not JWT_AVAILABLE:
        raise TokenError("PyJWT not installed. Run: pip install PyJWT")
    
    algorithms = algorithms or ["HS256"]
    
    try:
        return jwt.decode(token, secret, algorithms=algorithms)
    except jwt.ExpiredSignatureError:
        raise TokenError("Token has expired")
    except jwt.InvalidTokenError as e:
        raise TokenError(f"Invalid token: {e}")


def create_refresh_token(
    user: User,
    secret: str,
    expires_in: timedelta = timedelta(days=30),
) -> str:
    """Create a long-lived refresh token."""
    return create_jwt(
        user, 
        secret, 
        expires_in=expires_in,
        extra_claims={"type": "refresh"}
    )


def create_access_token(
    user: User,
    secret: str,
    expires_in: timedelta = timedelta(minutes=15),
) -> str:
    """Create a short-lived access token."""
    return create_jwt(
        user,
        secret,
        expires_in=expires_in,
        extra_claims={"type": "access"}
    )
