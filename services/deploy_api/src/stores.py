"""
Higher-level store operations for deploy_api.

These wrap the auto-generated CRUD classes with business logic.
"""

import json
from datetime import datetime
from typing import Dict, Any, List, Optional

from .._gen.crud import (
    WorkspaceCRUD,
    WorkspaceMemberCRUD,
    ProjectCRUD,
    CredentialCRUD,
    DeploymentRunCRUD,
    DeploymentStateCRUD,
)


def _now() -> str:
    return datetime.utcnow().isoformat()


# =============================================================================
# Workspace Store
# =============================================================================

class WorkspaceStore:
    """
    Workspace operations with membership management.
    
    Wraps WorkspaceCRUD and WorkspaceMemberCRUD with higher-level logic.
    """
    
    def __init__(self, db):
        self.db = db
        self._workspace_crud = WorkspaceCRUD(db)
        self._member_crud = WorkspaceMemberCRUD(db)
    
    async def create(
        self,
        name: str,
        owner_id: str,
        plan: str = "free",
    ) -> Dict[str, Any]:
        """Create workspace and add owner as member."""
        workspace = await self._workspace_crud.create(name, owner_id, plan)
        
        # Add owner as member with 'owner' role
        await self._member_crud.create(
            workspace_id=workspace["id"],
            user_id=owner_id,
            role="owner",
        )
        
        return workspace
    
    async def get(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Get workspace by ID."""
        return await self._workspace_crud.get(workspace_id)
    
    async def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get workspace by name."""
        return await self._workspace_crud.find_by_name(name)
    
    async def list_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        """List workspaces user is a member of, with their role."""
        memberships = await self._member_crud.list_by_user(user_id)
        
        workspaces = []
        for m in memberships:
            ws = await self._workspace_crud.get(m["workspace_id"])
            if ws:
                ws["role"] = m["role"]
                workspaces.append(ws)
        
        return workspaces
    
    async def delete(self, workspace_id: str) -> bool:
        """Delete workspace (cascade deletes members via FK)."""
        return await self._workspace_crud.delete(workspace_id)
    
    async def add_member(
        self,
        workspace_id: str,
        user_id: str,
        role: str = "member",
    ) -> Dict[str, Any]:
        """Add member to workspace."""
        return await self._member_crud.create(workspace_id, user_id, role)
    
    async def remove_member(self, workspace_id: str, user_id: str) -> bool:
        """Remove member from workspace."""
        return await self._member_crud.delete_by_workspace_user(workspace_id, user_id)
    
    async def is_member(self, user_id: str, workspace_id: str) -> bool:
        """Check if user is member of workspace."""
        member = await self._member_crud.find(workspace_id, user_id)
        return member is not None
    
    async def is_owner(self, user_id: str, workspace_id: str) -> bool:
        """Check if user is owner of workspace."""
        workspace = await self._workspace_crud.get(workspace_id)
        return workspace and workspace.get("owner_id") == user_id
    
    async def get_role(self, user_id: str, workspace_id: str) -> Optional[str]:
        """Get user's role in workspace."""
        member = await self._member_crud.find(workspace_id, user_id)
        return member["role"] if member else None


# =============================================================================
# Project Store
# =============================================================================

class ProjectStore:
    """
    Project operations with config management.
    
    Handles the config_json field which stores services and environments.
    """
    
    def __init__(self, db):
        self.db = db
        self._crud = ProjectCRUD(db)
    
    async def create(
        self,
        workspace_id: str,
        name: str,
        docker_hub_user: str,
        version: str = "latest",
        created_by: str = None,
    ) -> Dict[str, Any]:
        """Create project with initial config structure."""
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
        
        return await self._crud.create(
            workspace_id=workspace_id,
            name=name,
            docker_hub_user=docker_hub_user,
            version=version,
            config_json=json.dumps(config),
            created_by=created_by,
        )
    
    async def get(self, workspace_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Get project by workspace and name."""
        return await self._crud.find_by_workspace_name(workspace_id, name)
    
    async def list(self, workspace_id: str) -> List[Dict[str, Any]]:
        """List all projects in workspace."""
        return await self._crud.list_by_workspace(workspace_id)
    
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
        return await self._crud.update(project["id"], **updates)
    
    async def delete(self, workspace_id: str, name: str) -> bool:
        """Delete project."""
        project = await self.get(workspace_id, name)
        if not project:
            return False
        return await self._crud.delete(project["id"])
    
    async def get_config(self, workspace_id: str, name: str) -> Optional[Dict[str, Any]]:
        """Get parsed project config."""
        project = await self.get(workspace_id, name)
        if not project:
            return None
        
        config_str = project.get("config_json", "{}")
        if isinstance(config_str, str):
            return json.loads(config_str)
        return config_str
    
    async def save_config(
        self,
        workspace_id: str,
        name: str,
        config: Dict[str, Any],
    ) -> bool:
        """Save project config."""
        project = await self.get(workspace_id, name)
        if not project:
            return False
        
        await self._crud.update(
            project["id"],
            config_json=json.dumps(config),
        )
        return True
    
    async def add_service(
        self,
        workspace_id: str,
        name: str,
        service_name: str,
        service_config: Dict[str, Any],
    ) -> bool:
        """Add a service to project config."""
        config = await self.get_config(workspace_id, name)
        if not config:
            return False
        
        if "project" not in config:
            config["project"] = {"services": {}}
        if "services" not in config["project"]:
            config["project"]["services"] = {}
        
        config["project"]["services"][service_name] = service_config
        return await self.save_config(workspace_id, name, config)
    
    async def remove_service(
        self,
        workspace_id: str,
        name: str,
        service_name: str,
    ) -> bool:
        """Remove a service from project config."""
        config = await self.get_config(workspace_id, name)
        if not config:
            return False
        
        services = config.get("project", {}).get("services", {})
        if service_name not in services:
            return False
        
        del services[service_name]
        return await self.save_config(workspace_id, name, config)


# =============================================================================
# Credentials Store
# =============================================================================

def _get_encryption_key() -> bytes:
    """Get encryption key from environment or generate from JWT secret."""
    import os
    import hashlib
    import base64
    
    # Try dedicated encryption key first
    key = os.environ.get("ENCRYPTION_KEY")
    if key:
        # Ensure it's 32 bytes for Fernet
        return base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    
    # Fall back to deriving from JWT secret
    jwt_secret = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
    return base64.urlsafe_b64encode(hashlib.sha256(jwt_secret.encode()).digest())


def _encrypt(data: str) -> str:
    """Encrypt string data."""
    try:
        from cryptography.fernet import Fernet
        key = _get_encryption_key()
        f = Fernet(key)
        return f.encrypt(data.encode()).decode()
    except ImportError:
        # cryptography not installed, store as-is with warning marker
        return f"UNENCRYPTED:{data}"


def _decrypt(data: str) -> str:
    """Decrypt string data."""
    # Handle unencrypted data (backwards compatibility or missing cryptography)
    if data.startswith("UNENCRYPTED:"):
        return data[12:]
    
    try:
        from cryptography.fernet import Fernet
        key = _get_encryption_key()
        f = Fernet(key)
        return f.decrypt(data.encode()).decode()
    except ImportError:
        # Can't decrypt without cryptography
        raise RuntimeError("cryptography package required to decrypt credentials")
    except Exception:
        # Might be old unencrypted data
        return data


class CredentialsStore:
    """
    Credentials storage with encryption.
    
    Uses Fernet symmetric encryption with key derived from ENCRYPTION_KEY
    or JWT_SECRET environment variable.
    """
    
    def __init__(self, db):
        self.db = db
        self._crud = CredentialCRUD(db)
    
    async def set(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
        credentials: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Store credentials (encrypted)."""
        encrypted_blob = _encrypt(json.dumps(credentials))
        return await self._crud.upsert(workspace_id, project_name, env, encrypted_blob)
    
    async def get(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve and decrypt credentials."""
        cred = await self._crud.find(workspace_id, project_name, env)
        if not cred or not cred.get("encrypted_blob"):
            return None
        
        decrypted = _decrypt(cred["encrypted_blob"])
        return json.loads(decrypted)
    
    async def delete(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
    ) -> bool:
        """Delete credentials."""
        cred_id = f"{workspace_id}_{project_name}_{env}"
        return await self._crud.delete(cred_id)
    
    async def exists(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
    ) -> bool:
        """Check if credentials exist."""
        return await self._crud.exists(workspace_id, project_name, env)


# =============================================================================
# Deployment Store
# =============================================================================

class DeploymentStore:
    """
    Deployment runs and state management.
    """
    
    def __init__(self, db):
        self.db = db
        self._run_crud = DeploymentRunCRUD(db)
        self._state_crud = DeploymentStateCRUD(db)
    
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
        return await self._run_crud.create(
            workspace_id=workspace_id,
            job_id=job_id,
            project_name=project_name,
            env=env,
            triggered_by=triggered_by,
            services=json.dumps(services) if services else None,
        )
    
    async def update_run(self, job_id: str, **updates) -> bool:
        """Update deployment run by job_id."""
        # Handle result serialization
        if "result" in updates and updates["result"] is not None:
            updates["result_json"] = json.dumps(updates.pop("result"))
        elif "result" in updates:
            updates.pop("result")
        
        return await self._run_crud.update_by_job_id(job_id, **updates)
    
    async def get_run(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get deployment run by job_id with parsed JSON."""
        run = await self._run_crud.find_by_job_id(job_id)
        if not run:
            return None
        
        # Parse JSON fields
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
        return await self._run_crud.list_by_workspace(
            workspace_id, project_name, env, limit
        )
    
    async def get_state(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
    ) -> Dict[str, Any]:
        """Get deployment state."""
        state = await self._state_crud.find(workspace_id, project_name, env)
        if not state:
            return {}
        
        state_json = state.get("state_json", "{}")
        if isinstance(state_json, str):
            return json.loads(state_json)
        return state_json
    
    async def save_state(
        self,
        workspace_id: str,
        project_name: str,
        env: str,
        state: Dict[str, Any],
        deployed_by: str = None,
    ) -> bool:
        """Save deployment state."""
        await self._state_crud.upsert(
            workspace_id=workspace_id,
            project_name=project_name,
            env=env,
            state_json=json.dumps(state),
            deployed_by=deployed_by,
        )
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
        """Save project configuration (user = workspace_id)."""
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
        """Save deployment state for a workspace (legacy format)."""
        # Old format: single file with all project/env states
        # New format: separate records per project/env
        for project_name, envs in state.items():
            if isinstance(envs, dict):
                for env, env_state in envs.items():
                    await self.deployment_store.save_state(user, project_name, env, env_state)
    
    async def load_deployment_state(self, user: str) -> Dict[str, Any]:
        """Load deployment state for a workspace (legacy format)."""
        states = await self.deployment_store._state_crud.list_by_workspace(user)
        
        result = {}
        for s in states:
            project = s["project_name"]
            env = s["env"]
            if project not in result:
                result[project] = {}
            
            state_json = s.get("state_json", "{}")
            if isinstance(state_json, str):
                result[project][env] = json.loads(state_json)
            else:
                result[project][env] = state_json
        
        return result
