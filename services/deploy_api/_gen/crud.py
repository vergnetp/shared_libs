"""
CRUD operations - AUTO-GENERATED from manifest.yaml
DO NOT EDIT - changes will be overwritten on regenerate
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


def _now() -> str:
    return datetime.utcnow().isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


class BaseCRUD:
    """Base CRUD with common operations."""
    
    table: str = ""
    
    def __init__(self, db):
        self.db = db
    
    async def get(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get entity by ID."""
        return await self.db.get_entity(self.table, entity_id)
    
    async def delete(self, entity_id: str) -> bool:
        """Delete entity by ID."""
        return await self.db.delete_entity(self.table, entity_id)


# =============================================================================
# Workspace CRUD
# =============================================================================

class WorkspaceCRUD(BaseCRUD):
    table = "workspaces"
    
    async def create(
        self,
        name: str,
        owner_id: str,
        plan: str = "free",
    ) -> Dict[str, Any]:
        """Create a new workspace."""
        now = _now()
        entity = {
            "id": _uuid(),
            "name": name,
            "owner_id": owner_id,
            "plan": plan,
            "created_at": now,
            "updated_at": now,
        }
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def update(self, entity_id: str, **updates) -> Optional[Dict[str, Any]]:
        """Update workspace fields."""
        entity = await self.get(entity_id)
        if not entity:
            return None
        for key, value in updates.items():
            if value is not None and key in entity:
                entity[key] = value
        entity["updated_at"] = _now()
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def find_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find workspace by name."""
        results = await self.db.find_entities(
            self.table,
            where_clause="name = ?",
            params=(name,),
            limit=1,
        )
        return results[0] if results else None
    
    async def list_by_owner(self, owner_id: str) -> List[Dict[str, Any]]:
        """List workspaces by owner."""
        return await self.db.find_entities(
            self.table,
            where_clause="owner_id = ?",
            params=(owner_id,),
            order_by="name ASC",
        )


# =============================================================================
# WorkspaceMember CRUD
# =============================================================================

class WorkspaceMemberCRUD(BaseCRUD):
    table = "workspace_members"
    
    async def create(
        self,
        workspace_id: str,
        user_id: str,
        role: str = "member",
        joined_at: str = None,
    ) -> Dict[str, Any]:
        """Create a new workspace member."""
        now = _now()
        entity = {
            "id": _uuid(),
            "workspace_id": workspace_id,
            "user_id": user_id,
            "role": role,
            "joined_at": joined_at or now,
            "created_at": now,
            "updated_at": now,
        }
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def update(self, entity_id: str, **updates) -> Optional[Dict[str, Any]]:
        """Update workspace member fields."""
        entity = await self.get(entity_id)
        if not entity:
            return None
        for key, value in updates.items():
            if value is not None and key in entity:
                entity[key] = value
        entity["updated_at"] = _now()
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def list_by_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        """List members of a workspace."""
        return await self.db.find_entities(
            self.table,
            where_clause="workspace_id = ?",
            params=(workspace_id,),
        )
    
    async def list_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """List workspace memberships for a user."""
        return await self.db.find_entities(
            self.table,
            where_clause="user_id = ?",
            params=(user_id,),
        )
    
    async def find(self, workspace_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Find specific membership."""
        results = await self.db.find_entities(
            self.table,
            where_clause="workspace_id = ? AND user_id = ?",
            params=(workspace_id, user_id),
            limit=1,
        )
        return results[0] if results else None
    
    async def delete_by_workspace_user(self, workspace_id: str, user_id: str) -> bool:
        """Delete membership by workspace and user."""
        member = await self.find(workspace_id, user_id)
        if member:
            return await self.delete(member["id"])
        return False


# =============================================================================
# Project CRUD
# =============================================================================

class ProjectCRUD(BaseCRUD):
    table = "projects"
    
    async def create(
        self,
        workspace_id: str,
        name: str,
        docker_hub_user: str,
        version: str = "latest",
        config_json: str = "{}",
        created_by: str = None,
    ) -> Dict[str, Any]:
        """Create a new project."""
        now = _now()
        entity = {
            "id": f"{workspace_id}_{name}",  # Composite key
            "workspace_id": workspace_id,
            "name": name,
            "docker_hub_user": docker_hub_user,
            "version": version,
            "config_json": config_json,
            "created_by": created_by or "system",
            "created_at": now,
            "updated_at": now,
        }
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def update(self, entity_id: str, **updates) -> Optional[Dict[str, Any]]:
        """Update project fields."""
        entity = await self.get(entity_id)
        if not entity:
            return None
        for key, value in updates.items():
            if value is not None and key in entity:
                entity[key] = value
        entity["updated_at"] = _now()
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def find_by_workspace_name(self, workspace_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Find project by workspace and name."""
        return await self.get(f"{workspace_id}_{name}")
    
    async def list_by_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        """List projects in a workspace."""
        return await self.db.find_entities(
            self.table,
            where_clause="workspace_id = ?",
            params=(workspace_id,),
            order_by="name ASC",
        )


# =============================================================================
# Credential CRUD
# =============================================================================

class CredentialCRUD(BaseCRUD):
    table = "credentials"
    
    async def create(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
        encrypted_blob: str = None,
    ) -> Dict[str, Any]:
        """Create credentials."""
        now = _now()
        entity = {
            "id": f"{workspace_id}_{project_name}_{env}",  # Composite key
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "encrypted_blob": encrypted_blob,
            "created_at": now,
            "updated_at": now,
        }
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def upsert(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
        encrypted_blob: str,
    ) -> Dict[str, Any]:
        """Create or update credentials."""
        entity_id = f"{workspace_id}_{project_name}_{env}"
        existing = await self.get(entity_id)
        if existing:
            existing["encrypted_blob"] = encrypted_blob
            existing["updated_at"] = _now()
            await self.db.save_entity(self.table, existing)
            return existing
        return await self.create(workspace_id, project_name, env, encrypted_blob)
    
    async def find(self, workspace_id: str, project_name: str, env: str) -> Optional[Dict[str, Any]]:
        """Find credentials by workspace, project, and env."""
        return await self.get(f"{workspace_id}_{project_name}_{env}")
    
    async def exists(self, workspace_id: str, project_name: str, env: str) -> bool:
        """Check if credentials exist."""
        cred = await self.find(workspace_id, project_name, env)
        return cred is not None


# =============================================================================
# DeploymentRun CRUD
# =============================================================================

class DeploymentRunCRUD(BaseCRUD):
    table = "deployment_runs"
    
    async def create(
        self,
        workspace_id: str,
        job_id: str,
        project_name: str,
        env: str,
        triggered_by: str,
        services: str = None,
        status: str = "queued",
    ) -> Dict[str, Any]:
        """Create a deployment run."""
        now = _now()
        entity = {
            "id": _uuid(),
            "workspace_id": workspace_id,
            "job_id": job_id,
            "project_name": project_name,
            "env": env,
            "services": services,
            "status": status,
            "triggered_by": triggered_by,
            "triggered_at": now,
            "started_at": None,
            "completed_at": None,
            "result_json": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
        }
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def update(self, entity_id: str, **updates) -> Optional[Dict[str, Any]]:
        """Update deployment run fields."""
        entity = await self.get(entity_id)
        if not entity:
            return None
        for key, value in updates.items():
            if key in entity:
                entity[key] = value
        entity["updated_at"] = _now()
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def find_by_job_id(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Find deployment run by job ID."""
        results = await self.db.find_entities(
            self.table,
            where_clause="job_id = ?",
            params=(job_id,),
            limit=1,
        )
        return results[0] if results else None
    
    async def update_by_job_id(self, job_id: str, **updates) -> bool:
        """Update deployment run by job ID."""
        run = await self.find_by_job_id(job_id)
        if not run:
            return False
        await self.update(run["id"], **updates)
        return True
    
    async def list_by_workspace(
        self,
        workspace_id: str,
        project_name: str = None,
        env: str = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List deployment runs."""
        conditions = ["workspace_id = ?"]
        params = [workspace_id]
        
        if project_name:
            conditions.append("project_name = ?")
            params.append(project_name)
        if env:
            conditions.append("env = ?")
            params.append(env)
        
        return await self.db.find_entities(
            self.table,
            where_clause=" AND ".join(conditions),
            params=tuple(params),
            order_by="triggered_at DESC",
            limit=limit,
        )


# =============================================================================
# DeploymentState CRUD
# =============================================================================

class DeploymentStateCRUD(BaseCRUD):
    table = "deployment_state"
    
    async def create(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
        state_json: str = "{}",
        last_deployed_by: str = None,
    ) -> Dict[str, Any]:
        """Create deployment state."""
        now = _now()
        entity = {
            "id": f"{workspace_id}_{project_name}_{env}",  # Composite key
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "state_json": state_json,
            "last_deployed_at": now if last_deployed_by else None,
            "last_deployed_by": last_deployed_by,
            "created_at": now,
            "updated_at": now,
        }
        await self.db.save_entity(self.table, entity)
        return entity
    
    async def upsert(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
        state_json: str,
        deployed_by: str = None,
    ) -> Dict[str, Any]:
        """Create or update deployment state."""
        entity_id = f"{workspace_id}_{project_name}_{env}"
        now = _now()
        existing = await self.get(entity_id)
        if existing:
            existing["state_json"] = state_json
            existing["updated_at"] = now
            if deployed_by:
                existing["last_deployed_at"] = now
                existing["last_deployed_by"] = deployed_by
            await self.db.save_entity(self.table, existing)
            return existing
        return await self.create(workspace_id, project_name, env, state_json, deployed_by)
    
    async def find(self, workspace_id: str, project_name: str, env: str) -> Optional[Dict[str, Any]]:
        """Find deployment state."""
        return await self.get(f"{workspace_id}_{project_name}_{env}")
    
    async def list_by_workspace(self, workspace_id: str) -> List[Dict[str, Any]]:
        """List all deployment states for a workspace."""
        return await self.db.find_entities(
            self.table,
            where_clause="workspace_id = ?",
            params=(workspace_id,),
        )
