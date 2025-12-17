"""
Database-backed store implementations using DatabaseManager.
"""
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from .base import UserStore, RoleStore
from ..models import User, Role, RoleAssignment

# Import from sibling module - adjust path as needed for your project structure
from databases import DatabaseManager


class DatabaseUserStore(UserStore):
    """User store backed by DatabaseManager."""
    
    def __init__(self, db_type: str, **db_kwargs):
        self._db_type = db_type
        self._db_kwargs = db_kwargs
    
    async def get_by_id(self, user_id: str) -> Optional[User]:
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            entity = await conn.get_entity("auth_users", user_id, deserialize=True)
            return self._to_user(entity) if entity else None
    
    async def get_by_email(self, email: str) -> Optional[User]:
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            results = await conn.find_entities(
                "auth_users",
                where_clause="[email] = ? AND [deleted_at] IS NULL",
                params=(email,),
                limit=1,
                deserialize=True
            )
            return self._to_user(results[0]) if results else None
    
    async def create(self, user: User) -> User:
        if not user.id:
            user.id = str(uuid.uuid4())
        user.created_at = datetime.utcnow()
        user.updated_at = datetime.utcnow()
        
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            entity = await conn.save_entity("auth_users", asdict(user))
            return self._to_user(entity)
    
    async def update(self, user: User) -> User:
        user.updated_at = datetime.utcnow()
        
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            entity = await conn.save_entity("auth_users", asdict(user))
            return self._to_user(entity)
    
    async def delete(self, user_id: str, hard: bool = False) -> bool:
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            if hard:
                return await conn.delete_entity("auth_users", user_id, permanent=True)
            else:
                return await conn.delete_entity("auth_users", user_id, permanent=False)
    
    async def list(
        self, 
        filters: Optional[dict] = None, 
        limit: int = 100, 
        offset: int = 0
    ) -> list[User]:
        where_parts = ["[deleted_at] IS NULL"]
        params = []
        
        if filters:
            if "is_active" in filters:
                where_parts.append("[is_active] = ?")
                params.append(filters["is_active"])
            if "email_contains" in filters:
                where_parts.append("[email] LIKE ?")
                params.append(f"%{filters['email_contains']}%")
        
        where_clause = " AND ".join(where_parts) if where_parts else None
        
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            results = await conn.find_entities(
                "auth_users",
                where_clause=where_clause,
                params=tuple(params) if params else None,
                limit=limit,
                offset=offset,
                deserialize=True
            )
            return [self._to_user(r) for r in results]
    
    def _to_user(self, entity: dict) -> User:
        return User(
            id=entity.get("id"),
            email=entity.get("email"),
            password_hash=entity.get("password_hash"),
            name=entity.get("name"),
            metadata=entity.get("metadata") or {},
            is_active=entity.get("is_active", True),
            created_at=entity.get("created_at"),
            updated_at=entity.get("updated_at"),
            deleted_at=entity.get("deleted_at"),
        )


class DatabaseRoleStore(RoleStore):
    """Role store backed by DatabaseManager."""
    
    def __init__(self, db_type: str, **db_kwargs):
        self._db_type = db_type
        self._db_kwargs = db_kwargs
    
    # --- Role CRUD ---
    
    async def get_role(self, role_id: str) -> Optional[Role]:
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            entity = await conn.get_entity("auth_roles", role_id, deserialize=True)
            return self._to_role(entity) if entity else None
    
    async def get_role_by_name(self, name: str) -> Optional[Role]:
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            results = await conn.find_entities(
                "auth_roles",
                where_clause="[name] = ?",
                params=(name,),
                limit=1,
                deserialize=True
            )
            return self._to_role(results[0]) if results else None
    
    async def create_role(self, role: Role) -> Role:
        if not role.id:
            role.id = str(uuid.uuid4())
        role.created_at = datetime.utcnow()
        
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            entity = await conn.save_entity("auth_roles", asdict(role))
            return self._to_role(entity)
    
    async def update_role(self, role: Role) -> Role:
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            entity = await conn.save_entity("auth_roles", asdict(role))
            return self._to_role(entity)
    
    async def delete_role(self, role_id: str) -> bool:
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            return await conn.delete_entity("auth_roles", role_id, permanent=True)
    
    async def list_roles(self) -> list[Role]:
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            results = await conn.find_entities("auth_roles", deserialize=True)
            return [self._to_role(r) for r in results]
    
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
        
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            entity = await conn.save_entity("auth_role_assignments", asdict(assignment))
            return self._to_assignment(entity)
    
    async def revoke_role(
        self,
        user_id: str,
        role_id: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> bool:
        where = "[user_id] = ? AND [role_id] = ? AND [resource_type] = ?"
        params = [user_id, role_id, resource_type]
        
        if resource_id is None:
            where += " AND [resource_id] IS NULL"
        else:
            where += " AND [resource_id] = ?"
            params.append(resource_id)
        
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            results = await conn.find_entities(
                "auth_role_assignments",
                where_clause=where,
                params=tuple(params),
                limit=1
            )
            if results:
                return await conn.delete_entity("auth_role_assignments", results[0]["id"], permanent=True)
            return False
    
    async def get_user_roles(
        self, 
        user_id: str, 
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None
    ) -> list[RoleAssignment]:
        where_parts = ["[user_id] = ?"]
        params = [user_id]
        
        if resource_type:
            where_parts.append("[resource_type] = ?")
            params.append(resource_type)
        
        if resource_id:
            where_parts.append("[resource_id] = ?")
            params.append(resource_id)
        
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            results = await conn.find_entities(
                "auth_role_assignments",
                where_clause=" AND ".join(where_parts),
                params=tuple(params),
                deserialize=True
            )
            return [self._to_assignment(r) for r in results]
    
    async def get_resource_users(
        self,
        resource_type: str,
        resource_id: str,
        role_id: Optional[str] = None
    ) -> list[RoleAssignment]:
        where_parts = ["[resource_type] = ?", "[resource_id] = ?"]
        params = [resource_type, resource_id]
        
        if role_id:
            where_parts.append("[role_id] = ?")
            params.append(role_id)
        
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            results = await conn.find_entities(
                "auth_role_assignments",
                where_clause=" AND ".join(where_parts),
                params=tuple(params),
                deserialize=True
            )
            return [self._to_assignment(r) for r in results]
    
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
        # Get all role assignments for user on this resource (+ global)
        where = "[user_id] = ? AND [resource_type] = ?"
        params = [user_id, resource_type]
        
        if resource_id:
            where += " AND ([resource_id] = ? OR [resource_id] IS NULL)"
            params.append(resource_id)
        else:
            where += " AND [resource_id] IS NULL"
        
        async with DatabaseManager.connect(self._db_type, **self._db_kwargs) as conn:
            assignments = await conn.find_entities(
                "auth_role_assignments",
                where_clause=where,
                params=tuple(params),
                deserialize=True
            )
            
            # Collect all permissions from all assigned roles
            permissions = set()
            for assignment in assignments:
                role = await conn.get_entity("auth_roles", assignment["role_id"], deserialize=True)
                if role and role.get("permissions"):
                    permissions.update(role["permissions"])
            
            return list(permissions)
    
    def _to_role(self, entity: dict) -> Role:
        return Role(
            id=entity.get("id"),
            name=entity.get("name"),
            permissions=entity.get("permissions") or [],
            description=entity.get("description"),
            created_at=entity.get("created_at"),
        )
    
    def _to_assignment(self, entity: dict) -> RoleAssignment:
        return RoleAssignment(
            id=entity.get("id"),
            user_id=entity.get("user_id"),
            role_id=entity.get("role_id"),
            resource_type=entity.get("resource_type"),
            resource_id=entity.get("resource_id"),
            granted_by=entity.get("granted_by"),
            created_at=entity.get("created_at"),
            expires_at=entity.get("expires_at"),
        )
