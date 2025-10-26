"""
Project configuration storage with pluggable backends.

Supports multiple storage backends:
- FileSystemStorage: JSON files (default, current behavior)
- DatabaseStorage: PostgreSQL/SQLite (future)
- InMemoryStorage: Testing/development

Usage:
    # Use default filesystem backend
    storage = ConfigStorage.get_instance()
    
    # Or specify backend explicitly
    storage = ConfigStorage.get_instance(backend='filesystem')
    
    # Then use it
    storage.save_project(user, project_name, config_data)
    config = storage.load_project(user, project_name)
    projects = storage.list_projects(user)
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Dict, Any, Optional
import json
import os


class StorageBackend(ABC):
    """Abstract base class for storage backends"""
    
    @abstractmethod
    def save_project(self, user: str, project_name: str, config: Dict[str, Any]) -> None:
        """Save project configuration"""
        pass
    
    @abstractmethod
    def load_project(self, user: str, project_name: str) -> Dict[str, Any]:
        """Load project configuration"""
        pass
    
    @abstractmethod
    def list_projects(self, user: str) -> List[str]:
        """List all projects for a user"""
        pass
    
    @abstractmethod
    def delete_project(self, user: str, project_name: str) -> bool:
        """Delete project configuration"""
        pass
    
    @abstractmethod
    def project_exists(self, user: str, project_name: str) -> bool:
        """Check if project exists"""
        pass
    
    @abstractmethod
    def save_deployment_state(self, user: str, state: Dict[str, Any]) -> None:
        """Save deployment state for a user"""
        pass
    
    @abstractmethod
    def load_deployment_state(self, user: str) -> Dict[str, Any]:
        """Load deployment state for a user"""
        pass


class FileSystemStorage(StorageBackend):
    """
    Filesystem-based storage using JSON files.
    
    Structure: config/{user}/projects/{project_name}.json
    """
    
    def __init__(self, base_path: Optional[Path] = None):
        """
        Initialize filesystem storage.
        
        Args:
            base_path: Base directory for storage (default: ./config)
        """
        if base_path is None:
            # Default to config/ directory in infra folder
            base_path = Path(__file__).resolve().parent / 'config'
        self.base_path = Path(base_path)
    
    def _get_projects_path(self, user: str) -> Path:
        """Get path to user's projects directory"""
        folder = self.base_path / user / 'projects'
        folder.mkdir(exist_ok=True, parents=True)
        return folder
    
    def _get_deployment_state_path(self, user: str) -> Path:
        """Get path to user's deployment state file"""
        folder = self.base_path / user
        folder.mkdir(exist_ok=True, parents=True)
        return folder / 'deployments.json'
    
    def _get_project_path(self, user: str, project_name: str) -> Path:
        """Get path to specific project file"""
        return self._get_projects_path(user) / f"{project_name}.json"
    
    def save_project(self, user: str, project_name: str, config: Dict[str, Any]) -> None:
        """Save project configuration to JSON file"""
        project_path = self._get_project_path(user, project_name)
        project_path.parent.mkdir(exist_ok=True, parents=True)
        
        with project_path.open('w') as f:
            json.dump(config, f, indent=4)
    
    def load_project(self, user: str, project_name: str) -> Dict[str, Any]:
        """Load project configuration from JSON file"""
        project_path = self._get_project_path(user, project_name)
        
        if not project_path.exists():
            available = self.list_projects(user)
            if available:
                raise FileNotFoundError(
                    f"Project '{project_name}' not found for user '{user}'. "
                    f"Available: {', '.join(available)}"
                )
            else:
                raise FileNotFoundError(
                    f"Project '{project_name}' not found for user '{user}'. "
                    f"No projects in config/{user}/projects/"
                )
        
        try:
            with project_path.open('r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {project_path}: {e}")
    
    def list_projects(self, user: str) -> List[str]:
        """List all projects for a user"""
        projects_path = self._get_projects_path(user)
        if not projects_path.exists():
            return []
        
        return sorted([f.stem for f in projects_path.glob("*.json")])
    
    def delete_project(self, user: str, project_name: str) -> bool:
        """Delete project configuration file"""
        project_path = self._get_project_path(user, project_name)
        if project_path.exists():
            project_path.unlink()
            return True
        return False
    
    def project_exists(self, user: str, project_name: str) -> bool:
        """Check if project exists"""
        return self._get_project_path(user, project_name).exists()
    
    def save_deployment_state(self, user: str, state: Dict[str, Any]) -> None:
        """Save deployment state to JSON file"""
        state_path = self._get_deployment_state_path(user)
        state_path.parent.mkdir(exist_ok=True, parents=True)
        
        with state_path.open('w') as f:
            json.dump(state, f, indent=2)
    
    def load_deployment_state(self, user: str) -> Dict[str, Any]:
        """Load deployment state from JSON file"""
        state_path = self._get_deployment_state_path(user)
        
        if not state_path.exists():
            return {}
        
        try:
            with state_path.open('r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {state_path}: {e}")


class DatabaseStorage(StorageBackend):
    """
    Database-based storage (PostgreSQL/SQLite).
    
    Table schema:
        CREATE TABLE projects (
            user_id VARCHAR(50) NOT NULL,
            project_name VARCHAR(100) NOT NULL,
            config_json JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            PRIMARY KEY (user_id, project_name)
        );
    """
    
    def __init__(self, connection_string: str):
        """
        Initialize database storage.
        
        Args:
            connection_string: Database connection string
                Examples:
                - SQLite: "sqlite:///path/to/db.sqlite"
                - PostgreSQL: "postgresql://user:pass@host:5432/dbname"
        """
        self.connection_string = connection_string
        # TODO: Initialize database connection
        raise NotImplementedError("Database storage not yet implemented - coming soon!")
    
    def save_project(self, user: str, project_name: str, config: Dict[str, Any]) -> None:
        """Save project to database"""
        # TODO: Implement
        # INSERT INTO projects (user_id, project_name, config_json, updated_at)
        # VALUES (?, ?, ?, NOW())
        # ON CONFLICT (user_id, project_name) DO UPDATE SET config_json=?, updated_at=NOW()
        raise NotImplementedError()
    
    def load_project(self, user: str, project_name: str) -> Dict[str, Any]:
        """Load project from database"""
        # TODO: Implement
        # SELECT config_json FROM projects WHERE user_id=? AND project_name=?
        raise NotImplementedError()
    
    def list_projects(self, user: str) -> List[str]:
        """List user's projects from database"""
        # TODO: Implement
        # SELECT project_name FROM projects WHERE user_id=? ORDER BY project_name
        raise NotImplementedError()
    
    def delete_project(self, user: str, project_name: str) -> bool:
        """Delete project from database"""
        # TODO: Implement
        # DELETE FROM projects WHERE user_id=? AND project_name=?
        raise NotImplementedError()
    
    def project_exists(self, user: str, project_name: str) -> bool:
        """Check if project exists in database"""
        # TODO: Implement
        # SELECT COUNT(*) FROM projects WHERE user_id=? AND project_name=?
        raise NotImplementedError()
    
    def save_deployment_state(self, user: str, state: Dict[str, Any]) -> None:
        """Save deployment state to database"""
        # TODO: Implement
        # INSERT INTO deployment_states (user_id, state_json, updated_at)
        # VALUES (?, ?, NOW())
        # ON CONFLICT (user_id) DO UPDATE SET state_json=?, updated_at=NOW()
        raise NotImplementedError()
    
    def load_deployment_state(self, user: str) -> Dict[str, Any]:
        """Load deployment state from database"""
        # TODO: Implement
        # SELECT state_json FROM deployment_states WHERE user_id=?
        raise NotImplementedError()


class InMemoryStorage(StorageBackend):
    """
    In-memory storage for testing/development.
    Data is lost when process ends.
    """
    
    def __init__(self):
        """Initialize in-memory storage"""
        self._storage: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._deployment_states: Dict[str, Dict[str, Any]] = {}
    
    def save_project(self, user: str, project_name: str, config: Dict[str, Any]) -> None:
        """Save project to memory"""
        if user not in self._storage:
            self._storage[user] = {}
        self._storage[user][project_name] = config
    
    def load_project(self, user: str, project_name: str) -> Dict[str, Any]:
        """Load project from memory"""
        if user not in self._storage or project_name not in self._storage[user]:
            available = self.list_projects(user)
            if available:
                raise FileNotFoundError(
                    f"Project '{project_name}' not found for user '{user}'. "
                    f"Available: {', '.join(available)}"
                )
            else:
                raise FileNotFoundError(
                    f"Project '{project_name}' not found for user '{user}'. "
                    f"No projects in memory."
                )
        return self._storage[user][project_name]
    
    def list_projects(self, user: str) -> List[str]:
        """List user's projects in memory"""
        if user not in self._storage:
            return []
        return sorted(list(self._storage[user].keys()))
    
    def delete_project(self, user: str, project_name: str) -> bool:
        """Delete project from memory"""
        if user in self._storage and project_name in self._storage[user]:
            del self._storage[user][project_name]
            return True
        return False
    
    def project_exists(self, user: str, project_name: str) -> bool:
        """Check if project exists in memory"""
        return user in self._storage and project_name in self._storage[user]
    
    def save_deployment_state(self, user: str, state: Dict[str, Any]) -> None:
        """Save deployment state to memory"""
        self._deployment_states[user] = state
    
    def load_deployment_state(self, user: str) -> Dict[str, Any]:
        """Load deployment state from memory"""
        return self._deployment_states.get(user, {})


class ConfigStorage:
    """
    Main configuration storage interface with pluggable backends.
    
    Singleton pattern ensures single backend instance across application.
    """
    
    _instance: Optional['ConfigStorage'] = None
    _backend: Optional[StorageBackend] = None
    
    def __init__(self, backend: StorageBackend):
        """Initialize with specific backend (use get_instance() instead)"""
        self._backend = backend
    
    @classmethod
    def get_instance(cls, backend: str = 'filesystem', **backend_kwargs) -> 'ConfigStorage':
        """
        Get singleton storage instance.
        
        Args:
            backend: Backend type ('filesystem', 'database', 'memory')
            **backend_kwargs: Backend-specific configuration
                - filesystem: base_path (optional)
                - database: connection_string (required)
                - memory: (no args)
        
        Returns:
            ConfigStorage instance
        
        Examples:
            # Default filesystem backend
            storage = ConfigStorage.get_instance()
            
            # Filesystem with custom path
            storage = ConfigStorage.get_instance('filesystem', base_path='/custom/path')
            
            # Database backend
            storage = ConfigStorage.get_instance('database', 
                connection_string='postgresql://...')
            
            # In-memory for testing
            storage = ConfigStorage.get_instance('memory')
        """
        # Allow override via environment variable
        backend = os.getenv('CONFIG_STORAGE_BACKEND', backend)
        
        if cls._instance is None or cls._backend is None:
            if backend == 'filesystem':
                cls._backend = FileSystemStorage(**backend_kwargs)
            elif backend == 'database':
                cls._backend = DatabaseStorage(**backend_kwargs)
            elif backend == 'memory':
                cls._backend = InMemoryStorage(**backend_kwargs)
            else:
                raise ValueError(f"Unknown backend: {backend}. Use 'filesystem', 'database', or 'memory'")
            
            cls._instance = cls(cls._backend)
        
        return cls._instance
    
    @classmethod
    def reset_instance(cls):
        """Reset singleton (useful for testing)"""
        cls._instance = None
        cls._backend = None
    
    # Delegate all operations to backend
    
    def save_project(self, user: str, project_name: str, config: Dict[str, Any]) -> None:
        """Save project configuration"""
        return self._backend.save_project(user, project_name, config)
    
    def load_project(self, user: str, project_name: str) -> Dict[str, Any]:
        """Load project configuration"""
        return self._backend.load_project(user, project_name)
    
    def list_projects(self, user: str) -> List[str]:
        """List all projects for a user"""
        return self._backend.list_projects(user)
    
    def delete_project(self, user: str, project_name: str) -> bool:
        """Delete project configuration"""
        return self._backend.delete_project(user, project_name)
    
    def project_exists(self, user: str, project_name: str) -> bool:
        """Check if project exists"""
        return self._backend.project_exists(user, project_name)
    
    def save_deployment_state(self, user: str, state: Dict[str, Any]) -> None:
        """Save deployment state"""
        return self._backend.save_deployment_state(user, state)
    
    def load_deployment_state(self, user: str) -> Dict[str, Any]:
        """Load deployment state"""
        return self._backend.load_deployment_state(user)