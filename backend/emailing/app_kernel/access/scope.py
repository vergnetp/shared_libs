"""
Scope-based access control.

Provides fine-grained permission checks beyond simple membership.
Apps define their own permission logic; this module provides
the interface and utilities.

Usage:
    from app_kernel.access import ScopeChecker, require_scope
    
    # Define scopes
    class Scopes:
        READ = "read"
        WRITE = "write"
        DELETE = "delete"
        ADMIN = "admin"
    
    # Implement checker
    class MyPermissionChecker(ScopeChecker):
        async def has_scope(self, user_id: str, scope: str, resource_type: str, resource_id: str) -> bool:
            return await db.check_permission(user_id, scope, resource_type, resource_id)
    
    # Use in routes
    @app.delete("/documents/{doc_id}")
    async def delete_doc(
        doc_id: str,
        user: UserIdentity = Depends(require_scope("delete", "document"))
    ):
        ...
"""
from typing import Protocol, Optional, List, Callable, runtime_checkable
from functools import wraps

from fastapi import Request, HTTPException, Depends

from ..auth.models import UserIdentity
from ..auth.deps import get_current_user


@runtime_checkable
class ScopeChecker(Protocol):
    """
    Protocol for scope/permission checks.
    
    Apps implement this to provide their own permission logic.
    """
    
    async def has_scope(
        self, 
        user_id: str, 
        scope: str, 
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> bool:
        """Check if user has scope on resource."""
        ...
    
    async def get_scopes(
        self,
        user_id: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> List[str]:
        """Get all scopes user has on resource."""
        ...


class DefaultScopeChecker:
    """
    Default scope checker that allows all access.
    
    Apps should replace this with their own implementation.
    """
    
    async def has_scope(
        self, 
        user_id: str, 
        scope: str, 
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> bool:
        return True
    
    async def get_scopes(
        self,
        user_id: str,
        resource_type: str,
        resource_id: Optional[str] = None
    ) -> List[str]:
        return ["read", "write", "delete", "admin"]


class ScopeRegistry:
    """Registry for scope checker implementation."""
    
    def __init__(self):
        self._checker: ScopeChecker = DefaultScopeChecker()
    
    def set_checker(self, checker: ScopeChecker):
        """Set the scope checker implementation."""
        self._checker = checker
    
    @property
    def checker(self) -> ScopeChecker:
        return self._checker


# Global registry instance
scope_registry = ScopeRegistry()


def require_scope(
    scope: str,
    resource_type: str,
    resource_id_param: str = None
) -> Callable:
    """
    Create a dependency that requires a specific scope.
    
    Args:
        scope: Required scope (e.g., "read", "write", "delete")
        resource_type: Type of resource (e.g., "document", "project")
        resource_id_param: Name of path parameter containing resource ID.
                          If None, checks scope on resource type only.
    
    Usage:
        @app.get("/docs/{doc_id}")
        async def get_doc(
            doc_id: str,
            user: UserIdentity = Depends(require_scope("read", "document", "doc_id"))
        ):
            ...
    """
    async def dependency(
        request: Request,
        user: UserIdentity = Depends(get_current_user)
    ) -> UserIdentity:
        resource_id = None
        if resource_id_param:
            resource_id = request.path_params.get(resource_id_param)
        
        has_scope = await scope_registry.checker.has_scope(
            user.id, scope, resource_type, resource_id
        )
        
        if not has_scope:
            raise HTTPException(
                status_code=403,
                detail=f"Missing required scope: {scope} on {resource_type}"
            )
        
        return user
    
    return dependency


async def check_scope(
    user_id: str,
    scope: str,
    resource_type: str,
    resource_id: Optional[str] = None
) -> bool:
    """
    Programmatically check if user has scope.
    
    Use this for conditional logic, not route protection.
    """
    return await scope_registry.checker.has_scope(
        user_id, scope, resource_type, resource_id
    )


async def get_user_scopes(
    user_id: str,
    resource_type: str,
    resource_id: Optional[str] = None
) -> List[str]:
    """Get all scopes a user has on a resource."""
    return await scope_registry.checker.get_scopes(
        user_id, resource_type, resource_id
    )
