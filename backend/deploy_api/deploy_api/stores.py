"""
Database-backed stores for deploy API.

Implements StorageBackend interface from infra for seamless integration.
"""
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

from backend.app_kernel import get_logger

logger = get_logger()


def _now() -> str:
    return datetime.utcnow().isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


# =============================================================================
# Workspace Store
# =============================================================================

class WorkspaceStore:
    """Workspace (tenant) storage."""
    
    def __init__(self, db):
        self.db = db
    
    async def create(
        self,
        name: str,
        owner_id: str,
        plan: str = "free",
    ) -> Dict[str, Any]:
        """Create a new workspace."""
        workspace_id = _uuid()
        now = _now()
        
        workspace = {
            "id": workspace_id,
            "name": name,
            "owner_id": owner_id,
            "plan": plan,
            "created_at": now,
            "updated_at": now,
        }
        
        await self.db.save_entity("workspaces", workspace)
        
        # Add owner as member
        await self.db.save_entity("workspace_members", {
            "id": _uuid(),
            "workspace_id": workspace_id,
            "user_id": owner_id,
            "role": "owner",
            "joined_at": now,
        })
        
        logger.info(f"Created workspace {name}", extra={"workspace_id": workspace_id})
        return workspace
    
    async def get(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Get workspace by ID."""
        return await self.db.get_entity("workspaces", workspace_id)
    
    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get workspace by name."""
        results = await self.db.find_entities(
            "workspaces",
            where_clause="name = ?",
            params=(name,),
            limit=1,
        )
        return results[0] if results else None
    
    async def list_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """List workspaces user is a member of."""
        memberships = await self.db.find_entities(
            "workspace_members",
            where_clause="user_id = ?",
            params=(user_id,),
        )
        
        workspaces = []
        for m in memberships:
            ws = await self.get(m["workspace_id"])
            if ws:
                ws["role"] = m["role"]
                workspaces.append(ws)
        
        return workspaces
    
    async def delete(self, workspace_id: str) -> bool:
        """Delete workspace and all related data."""
        return await self.db.delete_entity("workspaces", workspace_id)
    
    async def add_member(
        self,
        workspace_id: str,
        user_id: str,
        role: str = "member",
    ) -> Dict[str, Any]:
        """Add member to workspace."""
        member = {
            "id": _uuid(),
            "workspace_id": workspace_id,
            "user_id": user_id,
            "role": role,
            "joined_at": _now(),
        }
        await self.db.save_entity("workspace_members", member)
        return member
    
    async def remove_member(self, workspace_id: str, user_id: str) -> bool:
        """Remove member from workspace."""
        members = await self.db.find_entities(
            "workspace_members",
            where_clause="workspace_id = ? AND user_id = ?",
            params=(workspace_id, user_id),
            limit=1,
        )
        if members:
            return await self.db.delete_entity("workspace_members", members[0]["id"])
        return False
    
    async def is_member(self, user_id: str, workspace_id: str) -> bool:
        """Check if user is member of workspace."""
        members = await self.db.find_entities(
            "workspace_members",
            where_clause="workspace_id = ? AND user_id = ?",
            params=(workspace_id, user_id),
            limit=1,
        )
        return len(members) > 0
    
    async def is_owner(self, user_id: str, workspace_id: str) -> bool:
        """Check if user is owner of workspace."""
        workspace = await self.get(workspace_id)
        return workspace and workspace.get("owner_id") == user_id
    
    async def get_role(self, user_id: str, workspace_id: str) -> Optional[str]:
        """Get user's role in workspace."""
        members = await self.db.find_entities(
            "workspace_members",
            where_clause="workspace_id = ? AND user_id = ?",
            params=(workspace_id, user_id),
            limit=1,
        )
        return members[0]["role"] if members else None


# =============================================================================
# Project Store
# =============================================================================

class ProjectStore:
    """Project storage - replaces config/{user}/projects/*.json"""
    
    def __init__(self, db):
        self.db = db
    
    async def create(
        self,
        workspace_id: str,
        name: str,
        docker_hub_user: str,
        version: str = "latest",
        created_by: str = None,
    ) -> Dict[str, Any]:
        """Create a new project."""
        project_id = f"{workspace_id}_{name}"
        now = _now()
        
        # Initial config structure matching infra expectations
        config = {
            "project": {
                "name": name,
                "docker_hub_user": docker_hub_user,
                "version": version,
                "services": {},
                "environments": {},
            }
        }
        
        project = {
            "id": project_id,
            "workspace_id": workspace_id,
            "name": name,
            "docker_hub_user": docker_hub_user,
            "version": version,
            "config_json": json.dumps(config),
            "created_at": now,
            "updated_at": now,
            "created_by": created_by or "system",
        }
        
        await self.db.save_entity("projects", project)
        logger.info(f"Created project {name}", extra={"workspace_id": workspace_id})
        return project
    
    async def get(self, workspace_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Get project by workspace and name."""
        project_id = f"{workspace_id}_{name}"
        return await self.db.get_entity("projects", project_id)
    
    async def list(self, workspace_id: str) -> List[Dict[str, Any]]:
        """List all projects in workspace."""
        return await self.db.find_entities(
            "projects",
            where_clause="workspace_id = ?",
            params=(workspace_id,),
            order_by="name ASC",
        )
    
    async def update(
        self,
        workspace_id: str,
        name: str,
        **updates,
    ) -> Optional[Dict[str, Any]]:
        """Update project fields."""
        project = await self.get(workspace_id, name)
        if not project:
            return None
        
        for key, value in updates.items():
            if key in ("docker_hub_user", "version"):
                project[key] = value
        
        project["updated_at"] = _now()
        await self.db.save_entity("projects", project)
        return project
    
    async def delete(self, workspace_id: str, name: str) -> bool:
        """Delete project."""
        project_id = f"{workspace_id}_{name}"
        return await self.db.delete_entity("projects", project_id)
    
    async def get_config(self, workspace_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Get full project config (for infra compatibility)."""
        project = await self.get(workspace_id, name)
        if not project:
            return None
        return json.loads(project.get("config_json", "{}"))
    
    async def save_config(
        self,
        workspace_id: str,
        name: str,
        config: Dict[str, Any],
    ) -> bool:
        """Save full project config."""
        project = await self.get(workspace_id, name)
        if not project:
            return False
        
        project["config_json"] = json.dumps(config)
        project["updated_at"] = _now()
        
        # Sync top-level fields
        if "project" in config:
            project["docker_hub_user"] = config["project"].get("docker_hub_user", project["docker_hub_user"])
            project["version"] = config["project"].get("version", project["version"])
        
        await self.db.save_entity("projects", project)
        return True
    
    async def add_service(
        self,
        workspace_id: str,
        project_name: str,
        service_name: str,
        service_config: Dict[str, Any],
    ) -> bool:
        """Add or update a service in project config."""
        config = await self.get_config(workspace_id, project_name)
        if not config:
            return False
        
        if "project" not in config:
            config["project"] = {"services": {}}
        if "services" not in config["project"]:
            config["project"]["services"] = {}
        
        config["project"]["services"][service_name] = service_config
        return await self.save_config(workspace_id, project_name, config)
    
    async def remove_service(
        self,
        workspace_id: str,
        project_name: str,
        service_name: str,
    ) -> bool:
        """Remove a service from project config."""
        config = await self.get_config(workspace_id, project_name)
        if not config:
            return False
        
        services = config.get("project", {}).get("services", {})
        if service_name in services:
            del services[service_name]
            return await self.save_config(workspace_id, project_name, config)
        return False


# =============================================================================
# Credentials Store
# =============================================================================

class CredentialsStore:
    """Encrypted credentials storage."""
    
    def __init__(self, db, encryption_key: str = None):
        self.db = db
        self._key = encryption_key
    
    def _encrypt(self, data: Dict[str, Any]) -> str:
        """Encrypt credentials. TODO: Use proper encryption."""
        # For now, just base64 encode. In production, use Fernet or similar.
        import base64
        return base64.b64encode(json.dumps(data).encode()).decode()
    
    def _decrypt(self, blob: str) -> Dict[str, Any]:
        """Decrypt credentials."""
        import base64
        return json.loads(base64.b64decode(blob.encode()).decode())
    
    async def set(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
        credentials: Dict[str, str],
    ) -> bool:
        """Store credentials (encrypted)."""
        cred_id = f"{workspace_id}_{project_name}_{env}"
        now = _now()
        
        existing = await self.db.get_entity("credentials", cred_id)
        
        cred = {
            "id": cred_id,
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "encrypted_blob": self._encrypt(credentials),
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
        }
        
        await self.db.save_entity("credentials", cred)
        logger.info(f"Stored credentials for {project_name}/{env}", extra={"workspace_id": workspace_id})
        return True
    
    async def get(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
    ) -> Optional[Dict[str, str]]:
        """Retrieve decrypted credentials."""
        cred_id = f"{workspace_id}_{project_name}_{env}"
        cred = await self.db.get_entity("credentials", cred_id)
        
        if not cred:
            return None
        
        return self._decrypt(cred["encrypted_blob"])
    
    async def delete(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
    ) -> bool:
        """Delete credentials."""
        cred_id = f"{workspace_id}_{project_name}_{env}"
        return await self.db.delete_entity("credentials", cred_id)
    
    async def exists(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
    ) -> bool:
        """Check if credentials exist."""
        cred_id = f"{workspace_id}_{project_name}_{env}"
        cred = await self.db.get_entity("credentials", cred_id)
        return cred is not None


# =============================================================================
# Deployment Store
# =============================================================================

class DeploymentStore:
    """Deployment runs and state storage."""
    
    def __init__(self, db):
        self.db = db
    
    async def create_run(
        self,
        job_id: str,
        workspace_id: str,
        project_name: str,
        env: str,
        triggered_by: str,
        services: List[str] = None,
    ) -> Dict[str, Any]:
        """Record a new deployment run."""
        run = {
            "id": _uuid(),
            "job_id": job_id,
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "services": json.dumps(services) if services else None,
            "status": "queued",
            "triggered_by": triggered_by,
            "triggered_at": _now(),
            "started_at": None,
            "completed_at": None,
            "result_json": None,
            "error": None,
        }
        
        await self.db.save_entity("deployment_runs", run)
        return run
    
    async def update_run(
        self,
        job_id: str,
        **updates,
    ) -> bool:
        """Update deployment run by job_id."""
        runs = await self.db.find_entities(
            "deployment_runs",
            where_clause="job_id = ?",
            params=(job_id,),
            limit=1,
        )
        
        if not runs:
            return False
        
        run = runs[0]
        for key, value in updates.items():
            if key == "result":
                run["result_json"] = json.dumps(value) if value else None
            elif key in run:
                run[key] = value
        
        await self.db.save_entity("deployment_runs", run)
        return True
    
    async def get_run(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get deployment run by job_id."""
        runs = await self.db.find_entities(
            "deployment_runs",
            where_clause="job_id = ?",
            params=(job_id,),
            limit=1,
        )
        
        if not runs:
            return None
        
        run = runs[0]
        if run.get("result_json"):
            run["result"] = json.loads(run["result_json"])
        if run.get("services"):
            run["services"] = json.loads(run["services"])
        return run
    
    async def list_runs(
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
            "deployment_runs",
            where_clause=" AND ".join(conditions),
            params=tuple(params),
            order_by="triggered_at DESC",
            limit=limit,
        )
    
    async def get_state(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
    ) -> Dict[str, Any]:
        """Get deployment state (replaces deployments.json)."""
        state_id = f"{workspace_id}_{project_name}_{env}"
        state = await self.db.get_entity("deployment_state", state_id)
        
        if not state:
            return {}
        
        return json.loads(state.get("state_json", "{}"))
    
    async def save_state(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
        state: Dict[str, Any],
        deployed_by: str = None,
    ) -> bool:
        """Save deployment state."""
        state_id = f"{workspace_id}_{project_name}_{env}"
        now = _now()
        
        existing = await self.db.get_entity("deployment_state", state_id)
        
        record = {
            "id": state_id,
            "workspace_id": workspace_id,
            "project_name": project_name,
            "env": env,
            "state_json": json.dumps(state),
            "last_deployed_at": now if deployed_by else (existing or {}).get("last_deployed_at"),
            "last_deployed_by": deployed_by or (existing or {}).get("last_deployed_by"),
            "updated_at": now,
        }
        
        await self.db.save_entity("deployment_state", record)
        return True


# =============================================================================
# Storage Backend Adapter (for infra compatibility)
# =============================================================================

class DatabaseStorageAdapter:
    """
    Adapts DB stores to infra's StorageBackend interface.
    
    This allows infra code to work with DB storage transparently.
    """
    
    def __init__(self, project_store: ProjectStore, deployment_store: DeploymentStore):
        self.project_store = project_store
        self.deployment_store = deployment_store
    
    async def save_project(self, user: str, project_name: str, config: Dict[str, Any]) -> None:
        """Save project configuration."""
        # user = workspace_id in new model
        await self.project_store.save_config(user, project_name, config)
    
    async def load_project(self, user: str, project_name: str) -> Dict[str, Any]:
        """Load project configuration."""
        config = await self.project_store.get_config(user, project_name)
        if not config:
            raise FileNotFoundError(f"Project '{project_name}' not found for workspace '{user}'")
        return config
    
    async def list_projects(self, user: str) -> List[str]:
        """List all projects for a workspace."""
        projects = await self.project_store.list(user)
        return [p["name"] for p in projects]
    
    async def delete_project(self, user: str, project_name: str) -> bool:
        """Delete project configuration."""
        return await self.project_store.delete(user, project_name)
    
    async def project_exists(self, user: str, project_name: str) -> bool:
        """Check if project exists."""
        project = await self.project_store.get(user, project_name)
        return project is not None
    
    async def save_deployment_state(self, user: str, state: Dict[str, Any]) -> None:
        """Save deployment state for a workspace."""
        # Old format: single file with all project/env states
        # New format: separate records per project/env
        # For compatibility, we extract and save each
        for project_name, envs in state.items():
            if isinstance(envs, dict):
                for env, env_state in envs.items():
                    await self.deployment_store.save_state(user, project_name, env, env_state)
    
    async def load_deployment_state(self, user: str) -> Dict[str, Any]:
        """Load deployment state for a workspace."""
        # Reconstruct old format from individual records
        # This is for compatibility with existing infra code
        # In practice, we should migrate infra to use granular APIs
        states = await self.deployment_store.db.find_entities(
            "deployment_state",
            where_clause="workspace_id = ?",
            params=(user,),
        )
        
        result = {}
        for s in states:
            project = s["project_name"]
            env = s["env"]
            if project not in result:
                result[project] = {}
            result[project][env] = json.loads(s.get("state_json", "{}"))
        
        return result
