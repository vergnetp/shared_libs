"""
File Storage Backend - JSON file-based storage.

Provides async interface over synchronous file operations.
Compatible with existing infra file structure.

Structure:
    config/{user}/projects/{project}.json    - Project configurations
    config/{user}/deployments.json           - Deployment state
    config/{user}/credentials/{env}.json     - Encrypted credentials
    config/{user}/servers.json               - Server inventory
"""

import json
import os
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

from .base import StorageBackend, StorageNotFoundError


class FileStorageBackend(StorageBackend):
    """
    JSON file-based storage backend.
    
    This is the default storage for standalone/CLI usage, maintaining
    compatibility with existing infra file structure.
    
    All async methods wrap synchronous file I/O using asyncio.to_thread()
    for non-blocking operation in async contexts.
    
    Usage:
        storage = FileStorageBackend(base_path="/config")
        
        # Or with default path
        storage = FileStorageBackend()
    """
    
    def __init__(
        self, 
        base_path: Optional[str] = None,
        encryption_key: Optional[str] = None,
    ):
        """
        Initialize file storage.
        
        Args:
            base_path: Root directory for storage (default: ./config)
            encryption_key: Key for encrypting credentials (optional)
        """
        if base_path is None:
            # Default to config/ directory in infra folder
            base_path = os.path.join(os.path.dirname(__file__), "..", "..", "config")
        
        self.base_path = Path(base_path).resolve()
        self.encryption_key = encryption_key
    
    # =========================================================================
    # Path Helpers
    # =========================================================================
    
    def _user_path(self, user_id: str) -> Path:
        """Get user's base directory."""
        path = self.base_path / user_id
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def _projects_path(self, user_id: str) -> Path:
        """Get user's projects directory."""
        path = self._user_path(user_id) / "projects"
        path.mkdir(parents=True, exist_ok=True)
        return path
    
    def _project_file(self, user_id: str, project_name: str) -> Path:
        """Get specific project file path."""
        return self._projects_path(user_id) / f"{project_name}.json"
    
    def _deployments_file(self, user_id: str) -> Path:
        """Get deployment state file path."""
        return self._user_path(user_id) / "deployments.json"
    
    def _credentials_file(self, user_id: str, project_name: str, env: str) -> Path:
        """Get credentials file path."""
        path = self._user_path(user_id) / "credentials" / project_name
        path.mkdir(parents=True, exist_ok=True)
        return path / f"{env}.json"
    
    def _servers_file(self, user_id: str) -> Path:
        """Get servers inventory file path."""
        return self._user_path(user_id) / "servers.json"
    
    # =========================================================================
    # File I/O Helpers
    # =========================================================================
    
    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        """Synchronous JSON read."""
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return None
    
    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        """Synchronous JSON write."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    
    def _delete_file(self, path: Path) -> bool:
        """Synchronous file delete."""
        if path.exists():
            path.unlink()
            return True
        return False
    
    async def _async_read(self, path: Path) -> Optional[Dict[str, Any]]:
        """Async wrapper for JSON read."""
        return await asyncio.to_thread(self._read_json, path)
    
    async def _async_write(self, path: Path, data: Dict[str, Any]) -> None:
        """Async wrapper for JSON write."""
        await asyncio.to_thread(self._write_json, path, data)
    
    async def _async_delete(self, path: Path) -> bool:
        """Async wrapper for file delete."""
        return await asyncio.to_thread(self._delete_file, path)
    
    # =========================================================================
    # Project Configuration
    # =========================================================================
    
    async def get_project(
        self, 
        user_id: str, 
        project_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get project configuration."""
        path = self._project_file(user_id, project_name)
        return await self._async_read(path)
    
    async def save_project(
        self, 
        user_id: str, 
        project_name: str, 
        config: Dict[str, Any]
    ) -> None:
        """Save project configuration."""
        path = self._project_file(user_id, project_name)
        
        # Add metadata
        config["_updated_at"] = datetime.utcnow().isoformat()
        if "_created_at" not in config:
            config["_created_at"] = config["_updated_at"]
        
        await self._async_write(path, config)
    
    async def delete_project(
        self, 
        user_id: str, 
        project_name: str
    ) -> bool:
        """Delete project configuration."""
        path = self._project_file(user_id, project_name)
        return await self._async_delete(path)
    
    async def list_projects(self, user_id: str) -> List[str]:
        """List all projects for a user."""
        def _list():
            projects_path = self._projects_path(user_id)
            if not projects_path.exists():
                return []
            return sorted([f.stem for f in projects_path.glob("*.json")])
        
        return await asyncio.to_thread(_list)
    
    # =========================================================================
    # Deployment State
    # =========================================================================
    
    async def get_deployment_state(
        self, 
        user_id: str, 
        project_name: str, 
        env: str
    ) -> Optional[Dict[str, Any]]:
        """Get deployment state for specific project/env."""
        path = self._deployments_file(user_id)
        all_states = await self._async_read(path) or {}
        
        # State is stored per project/env
        key = f"{project_name}/{env}"
        return all_states.get(key)
    
    async def save_deployment_state(
        self, 
        user_id: str, 
        project_name: str, 
        env: str, 
        state: Dict[str, Any]
    ) -> None:
        """Save deployment state."""
        path = self._deployments_file(user_id)
        all_states = await self._async_read(path) or {}
        
        # Update specific project/env
        key = f"{project_name}/{env}"
        state["_updated_at"] = datetime.utcnow().isoformat()
        all_states[key] = state
        
        await self._async_write(path, all_states)
    
    # =========================================================================
    # Credentials
    # =========================================================================
    
    async def get_credentials(
        self, 
        user_id: str, 
        project_name: str, 
        env: str
    ) -> Optional[Dict[str, Any]]:
        """Get credentials (decrypted if encryption enabled)."""
        path = self._credentials_file(user_id, project_name, env)
        data = await self._async_read(path)
        
        if data and self.encryption_key:
            # Decrypt if encrypted
            data = self._decrypt_credentials(data)
        
        return data
    
    async def save_credentials(
        self, 
        user_id: str, 
        project_name: str, 
        env: str, 
        credentials: Dict[str, Any]
    ) -> None:
        """Save credentials (encrypted if encryption enabled)."""
        path = self._credentials_file(user_id, project_name, env)
        
        data = credentials.copy()
        data["_updated_at"] = datetime.utcnow().isoformat()
        
        if self.encryption_key:
            # Encrypt before saving
            data = self._encrypt_credentials(data)
        
        await self._async_write(path, data)
    
    def _encrypt_credentials(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt credentials data."""
        # Simple encryption wrapper - can be enhanced
        try:
            from ..utils.encryption import Encryption
            encrypted = Encryption.encrypt(json.dumps(data), self.encryption_key)
            return {"_encrypted": True, "_data": encrypted}
        except ImportError:
            # Fallback: store as-is with warning marker
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
            # Return as-is if decryption fails
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
        """Get server inventory with optional filters."""
        path = self._servers_file(user_id)
        data = await self._async_read(path) or {"servers": []}
        
        servers = data.get("servers", [])
        
        # Apply filters
        if project_name:
            servers = [s for s in servers if s.get("project_name") == project_name]
        if env:
            servers = [s for s in servers if s.get("env") == env]
        if zone:
            servers = [s for s in servers if s.get("zone") == zone]
        
        return servers
    
    async def save_server(
        self, 
        user_id: str, 
        server: Dict[str, Any]
    ) -> None:
        """Save or update server in inventory."""
        path = self._servers_file(user_id)
        data = await self._async_read(path) or {"servers": []}
        
        servers = data.get("servers", [])
        server_id = server.get("id")
        
        # Update existing or append
        found = False
        for i, s in enumerate(servers):
            if s.get("id") == server_id:
                servers[i] = server
                found = True
                break
        
        if not found:
            servers.append(server)
        
        data["servers"] = servers
        data["_updated_at"] = datetime.utcnow().isoformat()
        
        await self._async_write(path, data)
    
    async def delete_server(
        self, 
        user_id: str, 
        server_id: str
    ) -> bool:
        """Remove server from inventory."""
        path = self._servers_file(user_id)
        data = await self._async_read(path) or {"servers": []}
        
        servers = data.get("servers", [])
        original_count = len(servers)
        servers = [s for s in servers if s.get("id") != server_id]
        
        if len(servers) < original_count:
            data["servers"] = servers
            data["_updated_at"] = datetime.utcnow().isoformat()
            await self._async_write(path, data)
            return True
        
        return False
    
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
        """Get deployment history."""
        path = self._user_path(user_id) / "history" / project_name / f"{env}.json"
        data = await self._async_read(path) or {"records": []}
        
        records = data.get("records", [])
        # Return most recent first, limited
        return records[-limit:][::-1]
    
    async def save_deployment_record(
        self,
        user_id: str,
        project_name: str,
        env: str,
        record: Dict[str, Any],
    ) -> None:
        """Append deployment record to history."""
        path = self._user_path(user_id) / "history" / project_name / f"{env}.json"
        data = await self._async_read(path) or {"records": []}
        
        record["_created_at"] = datetime.utcnow().isoformat()
        data["records"].append(record)
        
        # Keep last 100 records
        if len(data["records"]) > 100:
            data["records"] = data["records"][-100:]
        
        await self._async_write(path, data)
    
    # =========================================================================
    # Generic Key-Value
    # =========================================================================
    
    async def get(self, user_id: str, key: str) -> Optional[Any]:
        """Generic key-value get."""
        path = self._user_path(user_id) / "kv" / f"{key}.json"
        data = await self._async_read(path)
        return data.get("value") if data else None
    
    async def set(self, user_id: str, key: str, value: Any) -> None:
        """Generic key-value set."""
        path = self._user_path(user_id) / "kv" / f"{key}.json"
        await self._async_write(path, {
            "key": key,
            "value": value,
            "_updated_at": datetime.utcnow().isoformat(),
        })
    
    async def delete(self, user_id: str, key: str) -> bool:
        """Generic key-value delete."""
        path = self._user_path(user_id) / "kv" / f"{key}.json"
        return await self._async_delete(path)
