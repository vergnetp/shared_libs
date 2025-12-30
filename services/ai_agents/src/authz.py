"""
Centralized authorization helpers.

DESIGN PRINCIPLES:
1. No "fetch then check" - scope filters are built into queries
2. Admin bypass is explicit and centralized
3. Capabilities are checked in one place before side effects
4. All helpers return query components, not boolean checks after fetch

ADMIN BYPASS:
- `is_admin(user)` returns True for admins
- Query builders return unrestricted queries for admins
- This is the ONLY place admin logic lives

CAPABILITY ENFORCEMENT:
- `require_capability(agent, capability)` - call before any privileged action
- Capabilities are checked once, before any side effect
"""

from typing import Optional, List, Any, Tuple
from dataclasses import dataclass


# =============================================================================
# User Model
# =============================================================================

@dataclass
class CurrentUser:
    """
    Minimal user representation for authorization.
    
    All store methods require this. No exceptions.
    """
    id: str
    role: str = "user"  # 'admin' | 'user'
    
    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
    
    @classmethod
    def from_auth_user(cls, user: Any) -> "CurrentUser":
        """Create from auth module User object."""
        return cls(
            id=user.id,
            role=getattr(user, "role", None) or user.metadata.get("role", "user"),
        )


# =============================================================================
# Admin Bypass (Centralized)
# =============================================================================

def is_admin(user: CurrentUser) -> bool:
    """
    Check if user has admin role.
    
    Admins bypass ALL access checks. This is the single source of truth.
    """
    return user.role == "admin"


class ScopeError(Exception):
    """
    Raised when a resource access fails scope validation.
    
    Used by workers to indicate scope mismatch without leaking details.
    """
    def __init__(self, resource_type: str, resource_id: str, reason: str = "access denied"):
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.reason = reason
        super().__init__(f"{resource_type}:{resource_id} - {reason}")


def require_admin(user: CurrentUser) -> None:
    """
    Require user to be admin. Raises HTTPException if not.
    
    Usage:
        require_admin(current_user)  # Raises 403 if not admin
    """
    from fastapi import HTTPException
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")


async def verify_resource_scope(
    conn: Any,
    user: CurrentUser,
    resource_type: str,
    resource_id: str,
    expected_workspace_id: str = None,
) -> dict:
    """
    Load and verify resource scope. For workers.
    
    Checks:
    1. Resource exists
    2. Resource.workspace_id matches expected (if provided)
    3. User has access (owner or workspace member)
    
    Returns resource dict on success.
    Raises ScopeError on failure.
    
    Usage (in worker):
        doc = await verify_resource_scope(conn, user, "documents", doc_id, context.workspace_id)
    """
    resource = await conn.get_entity(resource_type, resource_id)
    
    if not resource:
        raise ScopeError(resource_type, resource_id, "not found")
    
    # Admin bypass
    if is_admin(user):
        return resource
    
    resource_workspace = resource.get("workspace_id")
    resource_owner = resource.get("owner_user_id")
    
    # Check expected workspace matches
    if expected_workspace_id:
        if resource_workspace and resource_workspace != expected_workspace_id:
            raise ScopeError(resource_type, resource_id, "workspace mismatch")
    
    # Check ownership or membership
    if resource_owner:
        if resource_owner == user.id:
            return resource  # Owner has access
    
    if resource_workspace:
        if await is_workspace_member(conn, user.id, resource_workspace):
            return resource  # Member has access
    
    raise ScopeError(resource_type, resource_id, "access denied")


def admin_or_filter(user: CurrentUser, filter_clause: str, params: tuple) -> Tuple[str, tuple]:
    """
    Return unrestricted query for admin, filtered query for regular users.
    
    This is the canonical pattern for admin bypass in queries.
    
    Usage:
        where, params = admin_or_filter(user, "[workspace_id] = ?", (ws_id,))
        # For admin: ("1=1", ())
        # For user:  ("[workspace_id] = ?", (ws_id,))
    """
    if is_admin(user):
        return ("1=1", ())
    return (filter_clause, params)


# =============================================================================
# Workspace Membership (Query Builders)
# =============================================================================

async def get_user_workspace_ids(conn: Any, user_id: str) -> List[str]:
    """Get all workspace IDs the user is a member of."""
    import logging
    logger = logging.getLogger(__name__)
    
    results = await conn.find_entities(
        "workspace_members",
        where_clause="[user_id] = ?",
        params=(user_id,),
    )
    workspace_ids = [r["workspace_id"] for r in results]
    logger.info(f"get_user_workspace_ids: user_id={user_id}, found {len(workspace_ids)} workspaces: {workspace_ids}")
    return workspace_ids


async def get_workspace_role(conn: Any, user_id: str, workspace_id: str) -> Optional[str]:
    """Get user's role in workspace. Returns 'owner', 'member', or None."""
    results = await conn.find_entities(
        "workspace_members",
        where_clause="[user_id] = ? AND [workspace_id] = ?",
        params=(user_id, workspace_id),
        limit=1,
    )
    return results[0]["role"] if results else None


async def is_workspace_member(conn: Any, user_id: str, workspace_id: str) -> bool:
    """Check if user is a member of the workspace."""
    role = await get_workspace_role(conn, user_id, workspace_id)
    return role is not None


async def is_workspace_owner(conn: Any, user_id: str, workspace_id: str) -> bool:
    """Check if user is owner of the workspace."""
    role = await get_workspace_role(conn, user_id, workspace_id)
    return role == "owner"


# =============================================================================
# Scoped Query Builders (Return WHERE clause + params)
# =============================================================================

async def workspace_scope(conn: Any, user: CurrentUser) -> Tuple[str, tuple]:
    """
    Build WHERE clause that restricts to user's workspaces.
    
    Admin: unrestricted
    User: only workspaces they're a member of
    """
    if is_admin(user):
        return ("1=1", ())
    
    workspace_ids = await get_user_workspace_ids(conn, user.id)
    
    if not workspace_ids:
        return ("1=0", ())  # No access
    
    placeholders = ",".join("?" * len(workspace_ids))
    return (f"[workspace_id] IN ({placeholders})", tuple(workspace_ids))


async def thread_scope(conn: Any, user: CurrentUser) -> Tuple[str, tuple]:
    """
    Build WHERE clause for thread access.
    
    Threads are accessed via workspace membership.
    """
    return await workspace_scope(conn, user)


async def agent_scope(conn: Any, user: CurrentUser) -> Tuple[str, tuple]:
    """
    Build WHERE clause for agent access.
    
    Agents are either:
    - Personal (owner_user_id = user.id)
    - Workspace (workspace_id in user's workspaces)
    """
    if is_admin(user):
        return ("1=1", ())
    
    workspace_ids = await get_user_workspace_ids(conn, user.id)
    
    if not workspace_ids:
        # Only personal agents
        return ("[owner_user_id] = ?", (user.id,))
    
    # Personal OR workspace agents
    placeholders = ",".join("?" * len(workspace_ids))
    return (
        f"([owner_user_id] = ? OR [workspace_id] IN ({placeholders}))",
        (user.id, *workspace_ids),
    )


async def document_scope(conn: Any, user: CurrentUser) -> Tuple[str, tuple]:
    """
    Build WHERE clause for document access.
    
    Documents are accessible if:
    - User is owner (owner_user_id = user.id)
    - OR visibility='workspace' AND user is member of workspace
    """
    if is_admin(user):
        return ("1=1", ())
    
    workspace_ids = await get_user_workspace_ids(conn, user.id)
    
    if not workspace_ids:
        # Only own documents
        return ("[owner_user_id] = ?", (user.id,))
    
    placeholders = ",".join("?" * len(workspace_ids))
    return (
        f"([owner_user_id] = ? OR ([visibility] = 'workspace' AND [workspace_id] IN ({placeholders})))",
        (user.id, *workspace_ids),
    )


# =============================================================================
# Single-Entity Access Check (for get by ID)
# =============================================================================

async def can_access_workspace(conn: Any, user: CurrentUser, workspace_id: str) -> bool:
    """Check if user can access workspace."""
    if is_admin(user):
        return True
    return await is_workspace_member(conn, user.id, workspace_id)


async def can_manage_workspace(conn: Any, user: CurrentUser, workspace_id: str) -> bool:
    """Check if user can manage workspace (add/remove members)."""
    if is_admin(user):
        return True
    return await is_workspace_owner(conn, user.id, workspace_id)


async def check_thread_access(conn: Any, user: CurrentUser, thread: dict) -> bool:
    """Check if user can access thread. Thread must have workspace_id."""
    if is_admin(user):
        return True
    workspace_id = thread.get("workspace_id")
    if not workspace_id:
        return False
    return await is_workspace_member(conn, user.id, workspace_id)


async def check_agent_access(conn: Any, user: CurrentUser, agent: dict) -> bool:
    """Check if user can access agent."""
    if is_admin(user):
        return True
    
    # Personal agent
    if agent.get("owner_user_id") == user.id:
        return True
    
    # Workspace agent
    workspace_id = agent.get("workspace_id")
    if workspace_id:
        return await is_workspace_member(conn, user.id, workspace_id)
    
    return False


async def check_document_access(conn: Any, user: CurrentUser, doc: dict) -> bool:
    """Check if user can access document."""
    if is_admin(user):
        return True
    
    # Owner always has access
    if doc.get("owner_user_id") == user.id:
        return True
    
    # Workspace visibility
    if doc.get("visibility") == "workspace" and doc.get("workspace_id"):
        return await is_workspace_member(conn, user.id, doc["workspace_id"])
    
    return False


# =============================================================================
# Capability Enforcement (Centralized)
# =============================================================================

class CapabilityError(Exception):
    """Raised when agent lacks required capability."""
    def __init__(self, capability: str, agent_id: str = None):
        self.capability = capability
        self.agent_id = agent_id
        super().__init__(f"Agent lacks capability: {capability}")


def require_capability(agent: dict, capability: str) -> None:
    """
    Check if agent has required capability.
    
    Call this ONCE before any privileged action.
    
    Raises:
        CapabilityError: If agent lacks the capability
    
    Usage:
        require_capability(agent, "moderate_content")
        # Now safe to moderate
    """
    capabilities = agent.get("capabilities") or []
    if isinstance(capabilities, str):
        import json
        try:
            capabilities = json.loads(capabilities)
        except:
            capabilities = []
    
    if capability not in capabilities:
        raise CapabilityError(capability, agent.get("id"))


def has_capability(agent: dict, capability: str) -> bool:
    """Check if agent has capability (non-throwing version)."""
    try:
        require_capability(agent, capability)
        return True
    except CapabilityError:
        return False


# =============================================================================
# Document Visibility Validation
# =============================================================================

class VisibilityError(Exception):
    """Raised when visibility constraints are violated."""
    pass


def validate_document_visibility(visibility: str, workspace_id: Optional[str]) -> None:
    """
    Enforce document visibility invariants.
    
    Rules:
    - visibility='private' → workspace_id must be NULL
    - visibility='workspace' → workspace_id must be NOT NULL
    
    Call this in document create/update BEFORE saving.
    
    Raises:
        VisibilityError: If invariant violated
    """
    if visibility == "workspace" and not workspace_id:
        raise VisibilityError("workspace_id required for visibility='workspace'")
    
    if visibility == "private" and workspace_id:
        raise VisibilityError("workspace_id must be NULL for visibility='private'")


def normalize_document_visibility(
    visibility: Optional[str],
    workspace_id: Optional[str],
) -> Tuple[str, Optional[str]]:
    """
    Normalize visibility/workspace_id to valid state.
    
    Returns (visibility, workspace_id) that satisfies invariants.
    """
    if workspace_id:
        return ("workspace", workspace_id)
    return ("private", None)


# =============================================================================
# Workspace Management Helpers
# =============================================================================

async def create_workspace(
    conn: Any,
    name: str,
    owner_user_id: str,
    description: str = None,
    metadata: dict = None,
) -> dict:
    """Create a new workspace with owner."""
    from datetime import datetime
    import uuid as uuid_mod
    
    now = datetime.utcnow().isoformat()
    workspace_id = str(uuid_mod.uuid4())
    
    workspace = await conn.save_entity("workspaces", {
        "id": workspace_id,
        "name": name,
        "description": description,
        "metadata": metadata or {},
        "created_at": now,
        "updated_at": now,
    })
    
    await conn.save_entity("workspace_members", {
        "id": str(uuid_mod.uuid4()),
        "workspace_id": workspace_id,
        "user_id": owner_user_id,
        "role": "owner",
        "created_at": now,
        "updated_at": now,
    })
    
    return workspace


async def add_workspace_member(
    conn: Any,
    workspace_id: str,
    user_id: str,
    role: str = "member",
) -> dict:
    """Add a user to a workspace."""
    from datetime import datetime
    import uuid as uuid_mod
    
    now = datetime.utcnow().isoformat()
    
    return await conn.save_entity("workspace_members", {
        "id": str(uuid_mod.uuid4()),
        "workspace_id": workspace_id,
        "user_id": user_id,
        "role": role,
        "created_at": now,
        "updated_at": now,
    })


async def remove_workspace_member(conn: Any, workspace_id: str, user_id: str) -> bool:
    """Remove a user from a workspace."""
    results = await conn.find_entities(
        "workspace_members",
        where_clause="[workspace_id] = ? AND [user_id] = ?",
        params=(workspace_id, user_id),
        limit=1,
    )
    
    if results:
        return await conn.delete_entity("workspace_members", results[0]["id"])
    return False


async def get_or_create_default_workspace(conn: Any, user: "CurrentUser") -> dict:
    """Get user's default workspace, creating one if needed. Returns workspace dict."""
    import logging
    logger = logging.getLogger(__name__)
    
    user_id = user.id if hasattr(user, 'id') else str(user)
    logger.info(f"get_or_create_default_workspace: user_id={user_id}")
    
    results = await conn.find_entities(
        "workspace_members",
        where_clause="[user_id] = ? AND [role] = 'owner'",
        params=(user_id,),
        limit=1,
    )
    logger.info(f"get_or_create_default_workspace: found {len(results)} owned workspaces")
    
    if results:
        # Get the full workspace
        workspace_id = results[0]["workspace_id"]
        logger.info(f"get_or_create_default_workspace: returning existing workspace {workspace_id}")
        workspaces = await conn.find_entities(
            "workspaces",
            where_clause="[id] = ?",
            params=(workspace_id,),
            limit=1,
        )
        if workspaces:
            return workspaces[0]
    
    # Create default workspace
    logger.info(f"get_or_create_default_workspace: creating new workspace for user {user_id}")
    workspace = await create_workspace(
        conn,
        name="Personal Workspace",
        owner_user_id=user_id,
        description="Default personal workspace",
    )
    logger.info(f"get_or_create_default_workspace: created workspace {workspace['id']}")
    
    return workspace
