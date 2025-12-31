"""
Built-in auth stores that use the kernel's database connection.

These are automatically used when:
- auth_enabled=True (default)
- database is configured
- no custom user_store is provided
"""

import json
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

from .router import UserStore


def _now() -> str:
    return datetime.utcnow().isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


class KernelUserStore(UserStore):
    """
    User store that uses the kernel's database connection.
    
    This is the default user store when database is configured.
    It implements the UserStore protocol expected by the auth router.
    """
    
    def __init__(self, get_db_connection):
        """
        Args:
            get_db_connection: The kernel's get_db_connection context manager
        """
        self._get_db = get_db_connection
    
    async def get_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get user by username (email)."""
        async with self._get_db() as db:
            # Use find_entities which is available on the connection
            results = await db.find_entities(
                "auth_users",
                where_clause="[email] = ?",
                params=(username,),
                limit=1,
                include_deleted=False,
                deserialize=True
            )
            if results:
                return self._entity_to_user(results[0])
            return None
    
    async def get_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by ID."""
        async with self._get_db() as db:
            # Use get_entity for ID lookup
            result = await db.get_entity(
                "auth_users",
                user_id,
                include_deleted=False,
                deserialize=True
            )
            if result:
                return self._entity_to_user(result)
            return None
    
    async def create(self, username: str, email: str, password_hash: str) -> Dict[str, Any]:
        """Create new user."""
        async with self._get_db() as db:
            # Check if email exists using find_entities
            existing = await db.find_entities(
                "auth_users",
                where_clause="[email] = ?",
                params=(email or username,),
                limit=1,
                include_deleted=True  # Check even deleted ones
            )
            if existing:
                raise ValueError(f"Email '{email or username}' already exists")
            
            now = _now()
            user_id = _uuid()
            
            # Use save_entity
            user_data = {
                "id": user_id,
                "email": email or username,
                "password_hash": password_hash,
                "name": username,
                "role": "user",
                "is_active": 1,
                "created_at": now,
                "updated_at": now,
            }
            
            await db.save_entity("auth_users", user_data)
            
            return {
                "id": user_id,
                "username": username,
                "email": email or username,
                "role": "user",
                "created_at": now,
            }
    
    async def update_password(self, user_id: str, password_hash: str) -> bool:
        """Update user's password hash."""
        async with self._get_db() as db:
            # Get existing user
            user = await db.get_entity("auth_users", user_id, include_deleted=False)
            if not user:
                return False
            
            # Update password
            user["password_hash"] = password_hash
            user["updated_at"] = _now()
            
            await db.save_entity("auth_users", user)
            return True
    
    def _entity_to_user(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """Convert database entity to user dict for auth router."""
        # Parse metadata if it's JSON string
        metadata = entity.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        
        return {
            "id": entity.get("id"),
            "username": entity.get("email"),  # email is username
            "email": entity.get("email"),
            "password_hash": entity.get("password_hash"),
            "name": entity.get("name"),
            "role": entity.get("role", "user"),
            "metadata": metadata or {},
            "is_active": bool(entity.get("is_active", True)),
            "created_at": entity.get("created_at"),
            "updated_at": entity.get("updated_at"),
        }


def create_kernel_user_store(get_db_connection) -> KernelUserStore:
    """
    Factory function to create a KernelUserStore.
    
    Args:
        get_db_connection: The kernel's get_db_connection context manager
        
    Returns:
        KernelUserStore instance
    """
    return KernelUserStore(get_db_connection)
