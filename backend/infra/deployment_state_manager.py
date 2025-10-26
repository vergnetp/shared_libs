import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .config_storage import ConfigStorage
except ImportError:
    from config_storage import ConfigStorage


def log(msg):
    Logger.log(msg)


class DeploymentStateManager:
    """
    Manage deployment state and history - which services are running where.
    
    Uses ConfigStorage backend for consistent storage with projects.
    Deployment state stored in dedicated deployments.json file per user.
    """
    
    MAX_HISTORY_PER_SERVICE = 10  # Keep last 10 deployments
    
    @staticmethod
    def _load_state(user: str) -> Dict[str, Any]:
        """Load deployment state using ConfigStorage"""
        storage = ConfigStorage.get_instance()
        return storage.load_deployment_state(user)
    
    @staticmethod
    def _save_state(user: str, state: Dict[str, Any]):
        """Save deployment state using ConfigStorage"""
        storage = ConfigStorage.get_instance()
        storage.save_deployment_state(user, state)
    
    @staticmethod
    def record_deployment(
        user: str,
        project: str,
        env: str,
        service: str,
        servers: List[str],
        container_name: str,
        version: str
    ):
        """Record a successful deployment with history tracking"""
        state = DeploymentStateManager._load_state(user)
        
        # Initialize structure if needed (user is implicit in state now)
        if project not in state:
            state[project] = {}
        if env not in state[project]:
            state[project][env] = {}
        if service not in state[project][env]:
            state[project][env][service] = {
                "current": None,
                "history": []
            }
        
        # Create deployment record
        deployment_record = {
            "servers": servers,
            "container_name": container_name,
            "version": version,
            "deployed_at": datetime.now().isoformat()
        }
        
        # Update current
        state[project][env][service]["current"] = deployment_record
        
        # Append to history (newest first)
        history = state[project][env][service]["history"]
        history.insert(0, deployment_record)
        
        # Trim history to max size
        state[project][env][service]["history"] = history[:DeploymentStateManager.MAX_HISTORY_PER_SERVICE]
        
        DeploymentStateManager._save_state(user, state)
        log(f"Recorded deployment: {user}/{project}/{env}/{service} v{version} on {len(servers)} servers")
    
    @staticmethod
    def get_current_deployment(
        user: str,
        project: str,
        env: str,
        service: str
    ) -> Optional[Dict[str, Any]]:
        """Get current deployment info for a service"""
        state = DeploymentStateManager._load_state(user)
        
        try:
            return state[project][env][service]["current"]
        except KeyError:
            return None
    
    @staticmethod
    def get_deployment_history(
        user: str,
        project: str,
        env: str,
        service: str
    ) -> List[Dict[str, Any]]:
        """Get deployment history for a service (newest first)"""
        state = DeploymentStateManager._load_state(user)
        
        try:
            return state[project][env][service]["history"]
        except KeyError:
            return []
    
    @staticmethod
    def get_previous_version(
        user: str,
        project: str,
        env: str,
        service: str
    ) -> Optional[str]:
        """Get the previous deployment version (second most recent)"""
        history = DeploymentStateManager.get_deployment_history(user, project, env, service)
        
        if len(history) < 2:
            return None
        
        return history[1].get("version")
    
    @staticmethod
    def get_version_history(
        user: str,
        project: str,
        env: str,
        service: str
    ) -> List[str]:
        """Get list of deployed versions (newest first, no duplicates)"""
        history = DeploymentStateManager.get_deployment_history(user, project, env, service)
        
        # Extract versions, remove duplicates while preserving order
        seen = set()
        versions = []
        for record in history:
            version = record.get("version")
            if version and version not in seen:
                seen.add(version)
                versions.append(version)
        
        return versions
    
    @staticmethod
    def get_all_services(
        user: str,
        project: Optional[str] = None,
        env: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get all deployed services for a user, optionally filtered by project/env"""
        state = DeploymentStateManager._load_state(user)
        
        if project and env:
            services = state.get(project, {}).get(env, {})
            # Return only current deployments for backward compatibility
            return {name: data["current"] for name, data in services.items() if data.get("current")}
        elif project:
            result = {}
            for env_name, services in state.get(project, {}).items():
                result[env_name] = {name: data["current"] for name, data in services.items() if data.get("current")}
            return result
        else:
            return state
    
    @staticmethod
    def update_service_servers(
        user: str,
        project: str,
        env: str,
        service: str,
        servers: List[str]
    ):
        """Update server list for current deployment"""
        state = DeploymentStateManager._load_state(user)
        
        try:
            state[project][env][service]["current"]["servers"] = servers
            state[project][env][service]["current"]["updated_at"] = datetime.now().isoformat()
            DeploymentStateManager._save_state(user, state)
            log(f"Updated servers for {user}/{project}/{env}/{service}")
        except KeyError:
            log(f"Warning: Service {user}/{project}/{env}/{service} not found in deployment state")
    
    @staticmethod
    def remove_server_from_all_services(user: str, server_ip: str):
        """Remove a server from all current service deployments for a user"""
        state = DeploymentStateManager._load_state(user)
        updated = False
        
        for project in state.values():
            for env in project.values():
                for service_name, service_data in env.items():
                    current = service_data.get("current")
                    if current and server_ip in current.get("servers", []):
                        current["servers"].remove(server_ip)
                        current["updated_at"] = datetime.now().isoformat()
                        updated = True
                        log(f"Removed {server_ip} from {service_name}")
        
        if updated:
            DeploymentStateManager._save_state(user, state)
    
    @staticmethod
    def add_server_to_service(
        user: str,
        project: str,
        env: str,
        service: str,
        server_ip: str
    ):
        """Add a server to current service deployment"""
        state = DeploymentStateManager._load_state(user)
        
        try:
            current = state[project][env][service]["current"]
            if server_ip not in current["servers"]:
                current["servers"].append(server_ip)
                current["updated_at"] = datetime.now().isoformat()
                DeploymentStateManager._save_state(user, state)
                log(f"Added {server_ip} to {project}/{env}/{service}")
        except KeyError:
            log(f"Warning: Service {project}/{env}/{service} not found in deployment state")
    
    @staticmethod
    def get_services_on_server(user: str, server_ip: str) -> List[Dict[str, str]]:
        """Get all services currently deployed on a specific server for a user"""
        state = DeploymentStateManager._load_state(user)
        services = []
        
        for project_name, project in state.items():
            for env_name, env in project.items():
                for service_name, service_data in env.items():
                    current = service_data.get("current")
                    if current and server_ip in current.get("servers", []):
                        services.append({
                            "user": user,
                            "project": project_name,
                            "env": env_name,
                            "service": service_name,
                            "container_name": current["container_name"],
                            "version": current.get("version", "unknown")
                        })
        
        return services