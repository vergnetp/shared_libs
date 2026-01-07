"""
SaaS module data stores.

Handles CRUD for workspaces, members, and invites.
"""

import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any


class WorkspaceStore:
    """CRUD operations for workspaces."""
    
    def __init__(self, conn):
        self.conn = conn
    
    async def create(
        self,
        name: str,
        owner_id: str,
        slug: str = None,
        is_personal: bool = False,
        settings: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Create a new workspace and add owner as member."""
        workspace_id = str(uuid.uuid4())
        slug = slug or self._generate_slug(name)
        
        workspace = {
            "id": workspace_id,
            "name": name,
            "slug": slug,
            "owner_id": owner_id,
            "is_personal": 1 if is_personal else 0,
            "settings_json": None,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        await self.conn.save_entity("workspaces", workspace)
        
        # Add owner as member with 'owner' role
        member_store = MemberStore(self.conn)
        await member_store.add(
            workspace_id=workspace_id,
            user_id=owner_id,
            role="owner",
        )
        
        return workspace
    
    async def get(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Get workspace by ID."""
        results = await self.conn.find_entities(
            "workspaces",
            where_clause="id = ?",
            params=(workspace_id,),
            limit=1,
        )
        return results[0] if results else None
    
    async def get_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get workspace by slug."""
        results = await self.conn.find_entities(
            "workspaces",
            where_clause="slug = ?",
            params=(slug,),
            limit=1,
        )
        return results[0] if results else None
    
    async def list_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """List all workspaces user is a member of."""
        # Get workspace IDs from membership
        members = await self.conn.find_entities(
            "workspace_members",
            where_clause="user_id = ?",
            params=(user_id,),
        )
        
        if not members:
            return []
        
        workspace_ids = [m["workspace_id"] for m in members]
        
        # Build role lookup
        role_by_ws = {m["workspace_id"]: m["role"] for m in members}
        
        # Get workspaces
        placeholders = ",".join(["?"] * len(workspace_ids))
        workspaces = await self.conn.find_entities(
            "workspaces",
            where_clause=f"id IN ({placeholders})",
            params=tuple(workspace_ids),
        )
        
        # Add role to each workspace
        for ws in workspaces:
            ws["role"] = role_by_ws.get(ws["id"], "member")
        
        return workspaces
    
    async def get_personal_workspace(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get user's personal workspace."""
        results = await self.conn.find_entities(
            "workspaces",
            where_clause="owner_id = ? AND is_personal = 1",
            params=(user_id,),
            limit=1,
        )
        return results[0] if results else None
    
    async def update(self, workspace_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update workspace."""
        workspace = await self.get(workspace_id)
        if not workspace:
            return None
        
        workspace.update(updates)
        workspace["updated_at"] = datetime.utcnow().isoformat()
        await self.conn.save_entity("workspaces", workspace)
        return workspace
    
    async def delete(self, workspace_id: str) -> bool:
        """Delete workspace and all members/invites."""
        # Delete members
        await self.conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id = ?",
            (workspace_id,),
        )
        # Delete invites
        await self.conn.execute(
            "DELETE FROM workspace_invites WHERE workspace_id = ?",
            (workspace_id,),
        )
        # Delete workspace
        await self.conn.execute(
            "DELETE FROM workspaces WHERE id = ?",
            (workspace_id,),
        )
        return True
    
    def _generate_slug(self, name: str) -> str:
        """Generate URL-friendly slug from name."""
        import re
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        # Add random suffix for uniqueness
        slug = f"{slug}-{secrets.token_hex(3)}"
        return slug


class MemberStore:
    """CRUD operations for workspace members."""
    
    def __init__(self, conn):
        self.conn = conn
    
    async def add(
        self,
        workspace_id: str,
        user_id: str,
        role: str = "member",
        invited_by: str = None,
    ) -> Dict[str, Any]:
        """Add a member to workspace."""
        # Check if already a member
        existing = await self.get(workspace_id, user_id)
        if existing:
            return existing
        
        member = {
            "id": str(uuid.uuid4()),
            "workspace_id": workspace_id,
            "user_id": user_id,
            "role": role,
            "invited_by": invited_by,
            "joined_at": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        await self.conn.save_entity("workspace_members", member)
        return member
    
    async def get(self, workspace_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get specific membership."""
        results = await self.conn.find_entities(
            "workspace_members",
            where_clause="workspace_id = ? AND user_id = ?",
            params=(workspace_id, user_id),
            limit=1,
        )
        return results[0] if results else None
    
    async def list_for_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        """List all members of a workspace."""
        return await self.conn.find_entities(
            "workspace_members",
            where_clause="workspace_id = ?",
            params=(workspace_id,),
        )
    
    async def update_role(self, workspace_id: str, user_id: str, role: str) -> Optional[Dict[str, Any]]:
        """Update member's role."""
        member = await self.get(workspace_id, user_id)
        if not member:
            return None
        
        member["role"] = role
        member["updated_at"] = datetime.utcnow().isoformat()
        await self.conn.save_entity("workspace_members", member)
        return member
    
    async def remove(self, workspace_id: str, user_id: str) -> bool:
        """Remove member from workspace."""
        await self.conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        )
        return True
    
    async def is_member(self, workspace_id: str, user_id: str) -> bool:
        """Check if user is a member."""
        member = await self.get(workspace_id, user_id)
        return member is not None
    
    async def is_admin(self, workspace_id: str, user_id: str) -> bool:
        """Check if user is admin or owner."""
        member = await self.get(workspace_id, user_id)
        return member is not None and member.get("role") in ("owner", "admin")
    
    async def is_owner(self, workspace_id: str, user_id: str) -> bool:
        """Check if user is owner."""
        member = await self.get(workspace_id, user_id)
        return member is not None and member.get("role") == "owner"


class InviteStore:
    """CRUD operations for workspace invites."""
    
    INVITE_EXPIRY_DAYS = 7
    
    def __init__(self, conn):
        self.conn = conn
    
    async def create(
        self,
        workspace_id: str,
        email: str,
        role: str = "member",
        invited_by: str = None,
    ) -> Dict[str, Any]:
        """Create a new invite."""
        # Check for existing pending invite
        existing = await self.get_pending_for_email(workspace_id, email)
        if existing:
            return existing
        
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=self.INVITE_EXPIRY_DAYS)
        
        invite = {
            "id": str(uuid.uuid4()),
            "workspace_id": workspace_id,
            "email": email.lower(),
            "role": role,
            "token": token,
            "invited_by": invited_by,
            "status": "pending",
            "expires_at": expires_at.isoformat(),
            "accepted_at": None,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        await self.conn.save_entity("workspace_invites", invite)
        return invite
    
    async def get(self, invite_id: str) -> Optional[Dict[str, Any]]:
        """Get invite by ID."""
        results = await self.conn.find_entities(
            "workspace_invites",
            where_clause="id = ?",
            params=(invite_id,),
            limit=1,
        )
        return results[0] if results else None
    
    async def get_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Get invite by token."""
        results = await self.conn.find_entities(
            "workspace_invites",
            where_clause="token = ?",
            params=(token,),
            limit=1,
        )
        return results[0] if results else None
    
    async def get_pending_for_email(self, workspace_id: str, email: str) -> Optional[Dict[str, Any]]:
        """Get pending invite for email in workspace."""
        results = await self.conn.find_entities(
            "workspace_invites",
            where_clause="workspace_id = ? AND email = ? AND status = 'pending'",
            params=(workspace_id, email.lower()),
            limit=1,
        )
        return results[0] if results else None
    
    async def list_for_workspace(self, workspace_id: str, status: str = None) -> List[Dict[str, Any]]:
        """List invites for workspace."""
        if status:
            return await self.conn.find_entities(
                "workspace_invites",
                where_clause="workspace_id = ? AND status = ?",
                params=(workspace_id, status),
            )
        return await self.conn.find_entities(
            "workspace_invites",
            where_clause="workspace_id = ?",
            params=(workspace_id,),
        )
    
    async def list_for_email(self, email: str) -> List[Dict[str, Any]]:
        """List pending invites for an email."""
        return await self.conn.find_entities(
            "workspace_invites",
            where_clause="email = ? AND status = 'pending'",
            params=(email.lower(),),
        )
    
    async def accept(self, token: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Accept an invite."""
        invite = await self.get_by_token(token)
        if not invite:
            return None
        
        if invite["status"] != "pending":
            return None
        
        # Check expiry
        expires_at = datetime.fromisoformat(invite["expires_at"])
        if datetime.utcnow() > expires_at:
            invite["status"] = "expired"
            await self.conn.save_entity("workspace_invites", invite)
            return None
        
        # Add user to workspace
        member_store = MemberStore(self.conn)
        await member_store.add(
            workspace_id=invite["workspace_id"],
            user_id=user_id,
            role=invite["role"],
            invited_by=invite["invited_by"],
        )
        
        # Update invite status
        invite["status"] = "accepted"
        invite["accepted_at"] = datetime.utcnow().isoformat()
        invite["updated_at"] = datetime.utcnow().isoformat()
        await self.conn.save_entity("workspace_invites", invite)
        
        return invite
    
    async def cancel(self, invite_id: str) -> bool:
        """Cancel an invite."""
        invite = await self.get(invite_id)
        if not invite:
            return False
        
        invite["status"] = "cancelled"
        invite["updated_at"] = datetime.utcnow().isoformat()
        await self.conn.save_entity("workspace_invites", invite)
        return True
    
    async def delete(self, invite_id: str) -> bool:
        """Delete an invite."""
        await self.conn.execute(
            "DELETE FROM workspace_invites WHERE id = ?",
            (invite_id,),
        )
        return True


class ProjectStore:
    """CRUD operations for projects within workspaces."""
    
    def __init__(self, conn):
        self.conn = conn
    
    async def create(
        self,
        workspace_id: str,
        name: str,
        created_by: str,
        slug: str = None,
        description: str = None,
        settings: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Create a new project in a workspace."""
        import json
        
        project_id = str(uuid.uuid4())
        slug = slug or self._generate_slug(name)
        
        # Check for duplicate slug in workspace
        existing = await self.get_by_slug(workspace_id, slug)
        if existing:
            raise ValueError(f"Project with slug '{slug}' already exists in workspace")
        
        project = {
            "id": project_id,
            "workspace_id": workspace_id,
            "name": name,
            "slug": slug,
            "description": description,
            "settings_json": json.dumps(settings) if settings else None,
            "created_by": created_by,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        
        await self.conn.save_entity("projects", project)
        return self._deserialize(project)
    
    async def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get project by ID."""
        results = await self.conn.find_entities(
            "projects",
            where_clause="id = ?",
            params=(project_id,),
            limit=1,
        )
        return self._deserialize(results[0]) if results else None
    
    async def get_by_slug(self, workspace_id: str, slug: str) -> Optional[Dict[str, Any]]:
        """Get project by slug within workspace."""
        results = await self.conn.find_entities(
            "projects",
            where_clause="workspace_id = ? AND slug = ?",
            params=(workspace_id, slug),
            limit=1,
        )
        return self._deserialize(results[0]) if results else None
    
    async def list_for_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        """List all projects in a workspace."""
        results = await self.conn.find_entities(
            "projects",
            where_clause="workspace_id = ?",
            params=(workspace_id,),
            order_by="name ASC",
        )
        return [self._deserialize(p) for p in results]
    
    async def update(
        self,
        project_id: str,
        updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Update project."""
        import json
        
        project = await self.conn.find_entities(
            "projects",
            where_clause="id = ?",
            params=(project_id,),
            limit=1,
        )
        if not project:
            return None
        
        project = project[0]
        
        # Handle settings serialization
        if "settings" in updates:
            updates["settings_json"] = json.dumps(updates.pop("settings"))
        
        project.update(updates)
        project["updated_at"] = datetime.utcnow().isoformat()
        await self.conn.save_entity("projects", project)
        return self._deserialize(project)
    
    async def delete(self, project_id: str) -> bool:
        """Delete a project."""
        await self.conn.execute(
            "DELETE FROM projects WHERE id = ?",
            (project_id,),
        )
        return True
    
    async def get_or_create_default(
        self,
        workspace_id: str,
        created_by: str,
    ) -> Dict[str, Any]:
        """Get the default project for a workspace, creating if needed."""
        projects = await self.list_for_workspace(workspace_id)
        
        if projects:
            # Return first project as default
            return projects[0]
        
        # Create default project
        return await self.create(
            workspace_id=workspace_id,
            name="Default",
            slug="default",
            created_by=created_by,
            description="Default project",
        )
    
    def _generate_slug(self, name: str) -> str:
        """Generate URL-friendly slug from name."""
        import re
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        return slug
    
    def _deserialize(self, project: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize settings_json to settings dict."""
        import json
        
        if project and project.get("settings_json"):
            try:
                project["settings"] = json.loads(project["settings_json"])
            except (json.JSONDecodeError, TypeError):
                project["settings"] = {}
        else:
            project["settings"] = {}
        
        return project
