"""
AuthService - Main authentication and authorization service.
"""
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple

from .models import User, Role, RoleAssignment
from .stores.base import UserStore, RoleStore
from .hashing import hash_password, verify_password
from .tokens import create_jwt, decode_jwt, create_access_token, create_refresh_token, TokenError


class AuthError(Exception):
    """Raised for authentication/authorization failures."""
    pass


class AuthService:
    """
    Main authentication and authorization service.
    
    Usage:
        from auth import AuthService, DatabaseUserStore, DatabaseRoleStore
        
        auth = AuthService(
            user_store=DatabaseUserStore("postgres", database="myapp"),
            role_store=DatabaseRoleStore("postgres", database="myapp"),
            token_secret=os.environ["JWT_SECRET"]
        )
        
        # Register
        user = await auth.register("alice@example.com", "password123")
        
        # Login
        user, token = await auth.login("alice@example.com", "password123")
        
        # Verify token
        user = await auth.verify_token(token)
        
        # Check permission
        can_edit = await auth.has_permission(user.id, "write", "project", "proj-123")
    """
    
    def __init__(
        self,
        user_store: UserStore,
        role_store: RoleStore,
        token_secret: str,
        access_token_expires: timedelta = timedelta(minutes=15),
        refresh_token_expires: timedelta = timedelta(days=30),
    ):
        self._users = user_store
        self._roles = role_store
        self._token_secret = token_secret
        self._access_token_expires = access_token_expires
        self._refresh_token_expires = refresh_token_expires
    
    # --- User Management ---
    
    async def register(
        self,
        email: str,
        password: str,
        name: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> User:
        """
        Register a new user.
        
        Raises:
            AuthError: If email already exists
        """
        existing = await self._users.get_by_email(email)
        if existing:
            raise AuthError("Email already registered")
        
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            password_hash=hash_password(password),
            name=name,
            metadata=metadata or {},
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        
        return await self._users.create(user)
    
    async def login(self, email: str, password: str) -> Tuple[User, str, str]:
        """
        Authenticate user and return tokens.
        
        Returns:
            Tuple of (user, access_token, refresh_token)
        
        Raises:
            AuthError: If credentials invalid or user inactive
        """
        user = await self._users.get_by_email(email)
        
        if not user:
            raise AuthError("Invalid credentials")
        
        if not user.password_hash:
            raise AuthError("User has no password (OAuth only)")
        
        if not verify_password(password, user.password_hash):
            raise AuthError("Invalid credentials")
        
        if not user.is_active:
            raise AuthError("User account is disabled")
        
        access_token = create_access_token(
            user, 
            self._token_secret, 
            self._access_token_expires
        )
        refresh_token = create_refresh_token(
            user,
            self._token_secret,
            self._refresh_token_expires
        )
        
        return user, access_token, refresh_token
    
    async def verify_token(self, token: str) -> User:
        """
        Verify a token and return the user.
        
        Raises:
            AuthError: If token invalid or user not found
        """
        try:
            payload = decode_jwt(token, self._token_secret)
        except TokenError as e:
            raise AuthError(str(e))
        
        user = await self._users.get_by_id(payload["sub"])
        
        if not user:
            raise AuthError("User not found")
        
        if not user.is_active:
            raise AuthError("User account is disabled")
        
        return user
    
    async def refresh_tokens(self, refresh_token: str) -> Tuple[str, str]:
        """
        Use refresh token to get new access + refresh tokens.
        
        Returns:
            Tuple of (new_access_token, new_refresh_token)
        """
        try:
            payload = decode_jwt(refresh_token, self._token_secret)
        except TokenError as e:
            raise AuthError(str(e))
        
        if payload.get("type") != "refresh":
            raise AuthError("Invalid token type")
        
        user = await self._users.get_by_id(payload["sub"])
        
        if not user or not user.is_active:
            raise AuthError("User not found or disabled")
        
        new_access = create_access_token(user, self._token_secret, self._access_token_expires)
        new_refresh = create_refresh_token(user, self._token_secret, self._refresh_token_expires)
        
        return new_access, new_refresh
    
    async def change_password(
        self,
        user_id: str,
        old_password: str,
        new_password: str
    ) -> bool:
        """Change user's password."""
        user = await self._users.get_by_id(user_id)
        
        if not user or not user.password_hash:
            raise AuthError("User not found")
        
        if not verify_password(old_password, user.password_hash):
            raise AuthError("Invalid current password")
        
        user.password_hash = hash_password(new_password)
        user.updated_at = datetime.utcnow()
        await self._users.update(user)
        
        return True
    
    async def reset_password(self, user_id: str, new_password: str) -> bool:
        """Reset user's password (admin action, no old password needed)."""
        user = await self._users.get_by_id(user_id)
        
        if not user:
            raise AuthError("User not found")
        
        user.password_hash = hash_password(new_password)
        user.updated_at = datetime.utcnow()
        await self._users.update(user)
        
        return True
    
    # --- Role Management ---
    
    async def create_role(
        self,
        name: str,
        permissions: list[str],
        description: Optional[str] = None
    ) -> Role:
        """Create a new role."""
        existing = await self._roles.get_role_by_name(name)
        if existing:
            raise AuthError(f"Role '{name}' already exists")
        
        role = Role(
            id=str(uuid.uuid4()),
            name=name,
            permissions=permissions,
            description=description,
        )
        return await self._roles.create_role(role)
    
    async def assign_role(
        self,
        user_id: str,
        role_name: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        granted_by: Optional[str] = None
    ) -> RoleAssignment:
        """Assign a role to a user on a resource."""
        role = await self._roles.get_role_by_name(role_name)
        if not role:
            raise AuthError(f"Role '{role_name}' not found")
        
        return await self._roles.assign_role(
            user_id=user_id,
            role_id=role.id,
            resource_type=resource_type,
            resource_id=resource_id,
            granted_by=granted_by
        )
    
    async def revoke_role(
        self,
        user_id: str,
        role_name: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> bool:
        """Revoke a role from a user."""
        role = await self._roles.get_role_by_name(role_name)
        if not role:
            raise AuthError(f"Role '{role_name}' not found")
        
        return await self._roles.revoke_role(
            user_id=user_id,
            role_id=role.id,
            resource_type=resource_type,
            resource_id=resource_id
        )
    
    # --- Permission Checks ---
    
    async def has_permission(
        self,
        user_id: str,
        permission: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> bool:
        """Check if user has permission on resource."""
        return await self._roles.has_permission(
            user_id=user_id,
            permission=permission,
            resource_type=resource_type,
            resource_id=resource_id
        )
    
    async def get_permissions(
        self,
        user_id: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> list[str]:
        """Get all permissions user has on resource."""
        return await self._roles.get_permissions(
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id
        )
    
    async def require_permission(
        self,
        user_id: str,
        permission: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> None:
        """
        Check permission, raise if denied.
        
        Raises:
            AuthError: If user lacks permission
        """
        has_perm = await self.has_permission(user_id, permission, resource_type, resource_id)
        if not has_perm:
            raise AuthError(f"Permission denied: {permission} on {resource_type}/{resource_id}")
