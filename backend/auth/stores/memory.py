"""
In-memory store implementations for testing.
"""
import uuid
from datetime import datetime
from typing import Optional

from .base import UserStore, RoleStore
from ..models import User, Role, RoleAssignment


class MemoryUserStore(UserStore):
    """In-memory user store for testing."""
    
    def __init__(self):
        self._users: dict[str, User] = {}
    
    async def get_by_id(self, user_id: str) -> Optional[User]:
        user = self._users.get(user_id)
        if user and user.deleted_at is None:
            return user
        return None
    
    async def get_by_email(self, email: str) -> Optional[User]:
        for user in self._users.values():
            if user.email == email and user.deleted_at is None:
                return user
        return None
    
    async def create(self, user: User) -> User:
        if not user.id:
            user.id = str(uuid.uuid4())
        user.created_at = datetime.utcnow()
        user.updated_at = datetime.utcnow()
        self._users[user.id] = user
        return user
    
    async def update(self, user: User) -> User:
        user.updated_at = datetime.utcnow()
        self._users[user.id] = user
        return user
    
    async def delete(self, user_id: str, hard: bool = False) -> bool:
        if user_id not in self._users:
            return False
        if hard:
            del self._users[user_id]
        else:
            self._users[user_id].deleted_at = datetime.utcnow()
        return True
    
    async def list(
        self, 
        filters: Optional[dict] = None, 
        limit: int = 100, 
        offset: int = 0
    ) -> list[User]:
        users = [u for u in self._users.values() if u.deleted_at is None]
        
        if filters:
            if "is_active" in filters:
                users = [u for u in users if u.is_active == filters["is_active"]]
            if "email_contains" in filters:
                users = [u for u in users if filters["email_contains"] in u.email]
        
        return users[offset:offset + limit]


class MemoryRoleStore(RoleStore):
    """In-memory role store for testing."""
    
    def __init__(self):
        self._roles: dict[str, Role] = {}
        self._assignments: dict[str, RoleAssignment] = {}
    
    # --- Role CRUD ---
    
    async def get_role(self, role_id: str) -> Optional[Role]:
        return self._roles.get(role_id)
    
    async def get_role_by_name(self, name: str) -> Optional[Role]:
        for role in self._roles.values():
            if role.name == name:
                return role
        return None
    
    async def create_role(self, role: Role) -> Role:
        if not role.id:
            role.id = str(uuid.uuid4())
        role.created_at = datetime.utcnow()
        self._roles[role.id] = role
        return role
    
    async def update_role(self, role: Role) -> Role:
        self._roles[role.id] = role
        return role
    
    async def delete_role(self, role_id: str) -> bool:
        if role_id in self._roles:
            del self._roles[role_id]
            return True
        return False
    
    async def list_roles(self) -> list[Role]:
        return list(self._roles.values())
    
    # --- Role Assignments ---
    
    async def assign_role(
        self,
        user_id: str,
        role_id: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        granted_by: Optional[str] = None
    ) -> RoleAssignment:
        assignment = RoleAssignment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            role_id=role_id,
            resource_type=resource_type,
            resource_id=resource_id,
            granted_by=granted_by,
            created_at=datetime.utcnow(),
        )
        self._assignments[assignment.id] = assignment
        return assignment
    
    async def revoke_role(
        self,
        user_id: str,
        role_id: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> bool:
        for aid, a in list(self._assignments.items()):
            if (a.user_id == user_id and 
                a.role_id == role_id and 
                a.resource_type == resource_type and 
                a.resource_id == resource_id):
                del self._assignments[aid]
                return True
        return False
    
    async def get_user_roles(
        self, 
        user_id: str, 
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None
    ) -> list[RoleAssignment]:
        results = []
        for a in self._assignments.values():
            if a.user_id != user_id:
                continue
            if resource_type and a.resource_type != resource_type:
                continue
            if resource_id and a.resource_id != resource_id:
                continue
            results.append(a)
        return results
    
    async def get_resource_users(
        self,
        resource_type: str,
        resource_id: str,
        role_id: Optional[str] = None
    ) -> list[RoleAssignment]:
        results = []
        for a in self._assignments.values():
            if a.resource_type != resource_type or a.resource_id != resource_id:
                continue
            if role_id and a.role_id != role_id:
                continue
            results.append(a)
        return results
    
    # --- Permission Checks ---
    
    async def has_permission(
        self,
        user_id: str,
        permission: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> bool:
        permissions = await self.get_permissions(user_id, resource_type, resource_id)
        return permission in permissions
    
    async def get_permissions(
        self,
        user_id: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> list[str]:
        permissions = set()
        
        for a in self._assignments.values():
            if a.user_id != user_id or a.resource_type != resource_type:
                continue
            # Match specific resource or global (None)
            if resource_id and a.resource_id not in (resource_id, None):
                continue
            if not resource_id and a.resource_id is not None:
                continue
            
            role = self._roles.get(a.role_id)
            if role:
                permissions.update(role.permissions)
        
        return list(permissions)
