"""
Abstract store interfaces for auth persistence.
"""
from abc import ABC, abstractmethod
from typing import Optional

from ..models import User, Role, RoleAssignment


class UserStore(ABC):
    """Abstract interface for user persistence."""
    
    @abstractmethod
    async def get_by_id(self, user_id: str) -> Optional[User]:
        """Get user by ID."""
        pass
    
    @abstractmethod
    async def get_by_email(self, email: str) -> Optional[User]:
        """Get user by email."""
        pass
    
    @abstractmethod
    async def create(self, user: User) -> User:
        """Create a new user. Returns user with ID populated."""
        pass
    
    @abstractmethod
    async def update(self, user: User) -> User:
        """Update existing user."""
        pass
    
    @abstractmethod
    async def delete(self, user_id: str, hard: bool = False) -> bool:
        """Delete user. Soft delete by default."""
        pass
    
    @abstractmethod
    async def list(
        self, 
        filters: Optional[dict] = None, 
        limit: int = 100, 
        offset: int = 0
    ) -> list[User]:
        """List users with optional filtering."""
        pass


class RoleStore(ABC):
    """Abstract interface for role and permission persistence."""
    
    # --- Role CRUD ---
    
    @abstractmethod
    async def get_role(self, role_id: str) -> Optional[Role]:
        """Get role by ID."""
        pass
    
    @abstractmethod
    async def get_role_by_name(self, name: str) -> Optional[Role]:
        """Get role by name."""
        pass
    
    @abstractmethod
    async def create_role(self, role: Role) -> Role:
        """Create a new role."""
        pass
    
    @abstractmethod
    async def update_role(self, role: Role) -> Role:
        """Update a role's permissions."""
        pass
    
    @abstractmethod
    async def delete_role(self, role_id: str) -> bool:
        """Delete a role."""
        pass
    
    @abstractmethod
    async def list_roles(self) -> list[Role]:
        """List all roles."""
        pass
    
    # --- Role Assignments ---
    
    @abstractmethod
    async def assign_role(
        self,
        user_id: str,
        role_id: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        granted_by: Optional[str] = None
    ) -> RoleAssignment:
        """Assign a role to user on a resource."""
        pass
    
    @abstractmethod
    async def revoke_role(
        self,
        user_id: str,
        role_id: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> bool:
        """Remove role from user."""
        pass
    
    @abstractmethod
    async def get_user_roles(
        self, 
        user_id: str, 
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None
    ) -> list[RoleAssignment]:
        """
        Get roles for a user.
        
        Args:
            user_id: The user
            resource_type: Filter by resource type (optional)
            resource_id: Filter by specific resource (optional)
        """
        pass
    
    @abstractmethod
    async def get_resource_users(
        self,
        resource_type: str,
        resource_id: str,
        role_id: Optional[str] = None
    ) -> list[RoleAssignment]:
        """
        Get all users with roles on a resource.
        
        Args:
            resource_type: The resource type
            resource_id: The resource ID
            role_id: Filter by specific role (optional)
        """
        pass
    
    # --- Permission Checks ---
    
    @abstractmethod
    async def has_permission(
        self,
        user_id: str,
        permission: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> bool:
        """
        Check if user has permission on resource.
        
        Checks both resource-specific and global (resource_id=None) assignments.
        """
        pass
    
    @abstractmethod
    async def get_permissions(
        self,
        user_id: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> list[str]:
        """Get all permissions user has on a resource."""
        pass
