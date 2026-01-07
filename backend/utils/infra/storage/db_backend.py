"""
Database Storage Backend - Entity-based database storage.

Integrates with app_kernel's database module for deploy_api.
Uses async database connections natively.

Entity Structure:
    - project: Project configurations
    - deployment_state: Current deployment state per env
    - credentials: Encrypted credentials per env
    - server: Server inventory
    - deployment_run: Deployment history
"""

import json
from typing import Dict, Any, Optional, List, Callable, Awaitable
from datetime import datetime

from .base import StorageBackend


class DatabaseStorageBackend(StorageBackend):
    """
    Database storage backend using entity system.
    
    Designed for use with deploy_api and app_kernel database module.
    All operations are natively async.
    
    Usage:
        from backend.app_kernel.db import get_db_connection
        
        storage = DatabaseStorageBackend(get_db_connection)
        
        # Use in context
        ctx = DeploymentContext(
            user_id="workspace_123",
            project_name="myapp",
            storage=storage,
        )
    """
    
    # Entity names (matching deploy_api schema)
    ENTITY_PROJECT = "project"
    ENTITY_STATE = "deployment_state"
    ENTITY_CREDENTIALS = "credentials"
    ENTITY_SERVER = "server"
    ENTITY_RUN = "deployment_run"
    
    def __init__(
        self,
        get_connection: Callable[[], Awaitable[Any]],
        encryption_key: Optional[str] = None,
    ):
        """
        Initialize database storage.
        
        Args:
            get_connection: Async context manager factory for DB connections.
                            Should return an object with find_entities/save_entity methods.
            encryption_key: Optional key for encrypting credentials
        """
        self.get_connection = get_connection
        self.encryption_key = encryption_key
    
    # =========================================================================
    # Project Configuration
    # =========================================================================
    
    async def get_project(
        self, 
        user_id: str, 
        project_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get project configuration from database."""
        async with self.get_connection() as conn:
            projects = await conn.find_entities(
                self.ENTITY_PROJECT,
                filters={
                    "workspace_id": user_id,
                    "name": project_name,
                },
                limit=1,
            )
            
            if not projects:
                return None
            
            project = projects[0]
            
            # Merge config_json into result if present
            if project.get("config_json"):
                if isinstance(project["config_json"], str):
                    config = json.loads(project["config_json"])
                else:
                    config = project["config_json"]
                return {**project, **config}
            
            return project
    
    async def save_project(
        self, 
        user_id: str, 
        project_name: str, 
        config: Dict[str, Any]
    ) -> None:
        """Save project configuration to database."""
        async with self.get_connection() as conn:
            # Check if exists
            existing = await conn.find_entities(
                self.ENTITY_PROJECT,
                filters={
                    "workspace_id": user_id,
                    "name": project_name,
                },
                limit=1,
            )
            
            now = datetime.utcnow().isoformat()
            
            if existing:
                # Update
                project = existing[0]
                project["config_json"] = json.dumps(config)
                project["updated_at"] = now
            else:
                # Create
                import uuid
                project = {
                    "id": str(uuid.uuid4()),
                    "workspace_id": user_id,
                    "name": project_name,
                    "config_json": json.dumps(config),
                    "created_at": now,
                    "updated_at": now,
                }
            
            await conn.save_entity(self.ENTITY_PROJECT, project)
    
    async def delete_project(
        self, 
        user_id: str, 
        project_name: str
    ) -> bool:
        """Delete project from database."""
        async with self.get_connection() as conn:
            projects = await conn.find_entities(
                self.ENTITY_PROJECT,
                filters={
                    "workspace_id": user_id,
                    "name": project_name,
                },
                limit=1,
            )
            
            if not projects:
                return False
            
            # Soft delete
            project = projects[0]
            project["deleted_at"] = datetime.utcnow().isoformat()
            await conn.save_entity(self.ENTITY_PROJECT, project)
            return True
    
    async def list_projects(self, user_id: str) -> List[str]:
        """List all projects for a user."""
        async with self.get_connection() as conn:
            projects = await conn.find_entities(
                self.ENTITY_PROJECT,
                filters={"workspace_id": user_id},
            )
            return [p["name"] for p in projects if not p.get("deleted_at")]
    
    # =========================================================================
    # Deployment State
    # =========================================================================
    
    async def get_deployment_state(
        self, 
        user_id: str, 
        project_name: str, 
        env: str
    ) -> Optional[Dict[str, Any]]:
        """Get deployment state from database."""
        async with self.get_connection() as conn:
            # First get project ID
            projects = await conn.find_entities(
                self.ENTITY_PROJECT,
                filters={
                    "workspace_id": user_id,
                    "name": project_name,
                },
                limit=1,
            )
            
            if not projects:
                return None
            
            project_id = projects[0]["id"]
            
            # Get state
            states = await conn.find_entities(
                self.ENTITY_STATE,
                filters={
                    "project_id": project_id,
                    "env": env,
                },
                limit=1,
            )
            
            if not states:
                return None
            
            state = states[0]
            
            # Parse state_json if present
            if state.get("state_json"):
                if isinstance(state["state_json"], str):
                    return json.loads(state["state_json"])
                return state["state_json"]
            
            return state
    
    async def save_deployment_state(
        self, 
        user_id: str, 
        project_name: str, 
        env: str, 
        state: Dict[str, Any]
    ) -> None:
        """Save deployment state to database."""
        async with self.get_connection() as conn:
            # Get project ID
            projects = await conn.find_entities(
                self.ENTITY_PROJECT,
                filters={
                    "workspace_id": user_id,
                    "name": project_name,
                },
                limit=1,
            )
            
            if not projects:
                raise ValueError(f"Project {project_name} not found for user {user_id}")
            
            project_id = projects[0]["id"]
            now = datetime.utcnow().isoformat()
            
            # Check if exists
            existing = await conn.find_entities(
                self.ENTITY_STATE,
                filters={
                    "project_id": project_id,
                    "env": env,
                },
                limit=1,
            )
            
            if existing:
                # Update
                db_state = existing[0]
                db_state["state_json"] = json.dumps(state)
                db_state["updated_at"] = now
            else:
                # Create
                import uuid
                db_state = {
                    "id": str(uuid.uuid4()),
                    "project_id": project_id,
                    "env": env,
                    "state_json": json.dumps(state),
                    "created_at": now,
                    "updated_at": now,
                }
            
            await conn.save_entity(self.ENTITY_STATE, db_state)
    
    # =========================================================================
    # Credentials
    # =========================================================================
    
    async def get_credentials(
        self, 
        user_id: str, 
        project_name: str, 
        env: str
    ) -> Optional[Dict[str, Any]]:
        """Get credentials from database."""
        async with self.get_connection() as conn:
            creds = await conn.find_entities(
                self.ENTITY_CREDENTIALS,
                filters={
                    "workspace_id": user_id,
                    "project_name": project_name,
                    "env": env,
                },
                limit=1,
            )
            
            if not creds:
                return None
            
            cred = creds[0]
            
            # Decrypt if encrypted
            if cred.get("credentials_json"):
                if isinstance(cred["credentials_json"], str):
                    data = json.loads(cred["credentials_json"])
                else:
                    data = cred["credentials_json"]
                
                if self.encryption_key and data.get("_encrypted"):
                    data = self._decrypt_credentials(data)
                
                return data
            
            return None
    
    async def save_credentials(
        self, 
        user_id: str, 
        project_name: str, 
        env: str, 
        credentials: Dict[str, Any]
    ) -> None:
        """Save credentials to database."""
        async with self.get_connection() as conn:
            now = datetime.utcnow().isoformat()
            
            # Encrypt if key provided
            data = credentials.copy()
            if self.encryption_key:
                data = self._encrypt_credentials(data)
            
            # Check if exists
            existing = await conn.find_entities(
                self.ENTITY_CREDENTIALS,
                filters={
                    "workspace_id": user_id,
                    "project_name": project_name,
                    "env": env,
                },
                limit=1,
            )
            
            if existing:
                cred = existing[0]
                cred["credentials_json"] = json.dumps(data)
                cred["updated_at"] = now
            else:
                import uuid
                cred = {
                    "id": str(uuid.uuid4()),
                    "workspace_id": user_id,
                    "project_name": project_name,
                    "env": env,
                    "credentials_json": json.dumps(data),
                    "created_at": now,
                    "updated_at": now,
                }
            
            await conn.save_entity(self.ENTITY_CREDENTIALS, cred)
    
    def _encrypt_credentials(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt credentials data."""
        try:
            from ..utils.encryption import Encryption
            encrypted = Encryption.encrypt(json.dumps(data), self.encryption_key)
            return {"_encrypted": True, "_data": encrypted}
        except ImportError:
            return {"_encrypted": False, **data}
    
    def _decrypt_credentials(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Decrypt credentials data."""
        if not data.get("_encrypted"):
            return data
        
        try:
            from ..utils.encryption import Encryption
            decrypted = Encryption.decrypt(data["_data"], self.encryption_key)
            return json.loads(decrypted)
        except (ImportError, Exception):
            return data
    
    # =========================================================================
    # Server Inventory
    # =========================================================================
    
    async def get_servers(
        self, 
        user_id: str, 
        project_name: Optional[str] = None,
        env: Optional[str] = None,
        zone: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get server inventory from database."""
        async with self.get_connection() as conn:
            filters = {"workspace_id": user_id}
            
            if project_name:
                filters["project_name"] = project_name
            if env:
                filters["env"] = env
            if zone:
                filters["zone"] = zone
            
            servers = await conn.find_entities(
                self.ENTITY_SERVER,
                filters=filters,
            )
            
            return [s for s in servers if not s.get("deleted_at")]
    
    async def save_server(
        self, 
        user_id: str, 
        server: Dict[str, Any]
    ) -> None:
        """Save server to database."""
        async with self.get_connection() as conn:
            now = datetime.utcnow().isoformat()
            
            # Check if exists by ID
            server_id = server.get("id")
            if server_id:
                existing = await conn.find_entities(
                    self.ENTITY_SERVER,
                    filters={"id": server_id},
                    limit=1,
                )
                
                if existing:
                    # Update
                    db_server = existing[0]
                    db_server.update(server)
                    db_server["updated_at"] = now
                    await conn.save_entity(self.ENTITY_SERVER, db_server)
                    return
            
            # Create new
            import uuid
            if not server.get("id"):
                server["id"] = str(uuid.uuid4())
            
            server["workspace_id"] = user_id
            server["created_at"] = now
            server["updated_at"] = now
            
            await conn.save_entity(self.ENTITY_SERVER, server)
    
    async def delete_server(
        self, 
        user_id: str, 
        server_id: str
    ) -> bool:
        """Delete server from database (soft delete)."""
        async with self.get_connection() as conn:
            servers = await conn.find_entities(
                self.ENTITY_SERVER,
                filters={
                    "workspace_id": user_id,
                    "id": server_id,
                },
                limit=1,
            )
            
            if not servers:
                return False
            
            server = servers[0]
            server["deleted_at"] = datetime.utcnow().isoformat()
            await conn.save_entity(self.ENTITY_SERVER, server)
            return True
    
    # =========================================================================
    # Deployment History
    # =========================================================================
    
    async def get_deployment_history(
        self,
        user_id: str,
        project_name: str,
        env: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get deployment history from database."""
        async with self.get_connection() as conn:
            # Get project ID first
            projects = await conn.find_entities(
                self.ENTITY_PROJECT,
                filters={
                    "workspace_id": user_id,
                    "name": project_name,
                },
                limit=1,
            )
            
            if not projects:
                return []
            
            project_id = projects[0]["id"]
            
            runs = await conn.find_entities(
                self.ENTITY_RUN,
                filters={
                    "project_id": project_id,
                    "env": env,
                },
                order_by="created_at DESC",
                limit=limit,
            )
            
            return runs
    
    async def save_deployment_record(
        self,
        user_id: str,
        project_name: str,
        env: str,
        record: Dict[str, Any],
    ) -> None:
        """Save deployment record to history."""
        async with self.get_connection() as conn:
            # Get project ID
            projects = await conn.find_entities(
                self.ENTITY_PROJECT,
                filters={
                    "workspace_id": user_id,
                    "name": project_name,
                },
                limit=1,
            )
            
            if not projects:
                raise ValueError(f"Project {project_name} not found")
            
            import uuid
            now = datetime.utcnow().isoformat()
            
            run = {
                "id": str(uuid.uuid4()),
                "project_id": projects[0]["id"],
                "env": env,
                "status": record.get("status", "completed"),
                "result_json": json.dumps(record) if record else None,
                "created_at": now,
            }
            
            await conn.save_entity(self.ENTITY_RUN, run)
