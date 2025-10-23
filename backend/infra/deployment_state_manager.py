import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

try:
    from .logger import Logger
except ImportError:
    from logger import Logger


def log(msg):
    Logger.log(msg)


class DeploymentStateManager:
    """Manage deployment state and history - which services are running where"""
    
    DEPLOYMENTS_FILE = Path("config/deployments.json")
    MAX_HISTORY_PER_SERVICE = 10  # Keep last 10 deployments
    
    @staticmethod
    def _load_state() -> Dict[str, Any]:
        """Load deployment state from file"""
        if not DeploymentStateManager.DEPLOYMENTS_FILE.exists():
            return {}
        
        try:
            return json.loads(DeploymentStateManager.DEPLOYMENTS_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log(f"Warning: Could not load deployments.json: {e}")
            return {}
    
    @staticmethod
    def _save_state(state: Dict[str, Any]):
        """Save deployment state to file"""
        DeploymentStateManager.DEPLOYMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        DeploymentStateManager.DEPLOYMENTS_FILE.write_text(json.dumps(state, indent=2))
    
    @staticmethod
    def record_deployment(
        project: str,
        env: str,
        service: str,
        servers: List[str],
        container_name: str,
        version: str
    ):
        """Record a successful deployment with history tracking"""
        state = DeploymentStateManager._load_state()
        
        # Initialize structure if needed
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
        
        DeploymentStateManager._save_state(state)
        log(f"Recorded deployment: {project}/{env}/{service} v{version} on {len(servers)} servers")
    
    @staticmethod
    def get_current_deployment(
        project: str,
        env: str,
        service: str
    ) -> Optional[Dict[str, Any]]:
        """Get current deployment info for a service"""
        state = DeploymentStateManager._load_state()
        
        try:
            return state[project][env][service]["current"]
        except KeyError:
            return None
    
    @staticmethod
    def get_deployment_history(
        project: str,
        env: str,
        service: str
    ) -> List[Dict[str, Any]]:
        """Get deployment history for a service (newest first)"""
        state = DeploymentStateManager._load_state()
        
        try:
            return state[project][env][service]["history"]
        except KeyError:
            return []
    
    @staticmethod
    def get_previous_version(
        project: str,
        env: str,
        service: str
    ) -> Optional[str]:
        """Get the previous deployment version (second most recent)"""
        history = DeploymentStateManager.get_deployment_history(project, env, service)
        
        if len(history) < 2:
            return None
        
        return history[1].get("version")
    
    @staticmethod
    def get_version_history(
        project: str,
        env: str,
        service: str
    ) -> List[str]:
        """Get list of deployed versions (newest first, no duplicates)"""
        history = DeploymentStateManager.get_deployment_history(project, env, service)
        
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
        project: Optional[str] = None,
        env: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get all deployed services, optionally filtered by project/env"""
        state = DeploymentStateManager._load_state()
        
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
        project: str,
        env: str,
        service: str,
        servers: List[str]
    ):
        """Update server list for current deployment"""
        state = DeploymentStateManager._load_state()
        
        try:
            state[project][env][service]["current"]["servers"] = servers
            state[project][env][service]["current"]["updated_at"] = datetime.now().isoformat()
            DeploymentStateManager._save_state(state)
            log(f"Updated servers for {project}/{env}/{service}")
        except KeyError:
            log(f"Warning: Service {project}/{env}/{service} not found in deployment state")
    
    @staticmethod
    def remove_server_from_all_services(server_ip: str):
        """Remove a server from all current service deployments"""
        state = DeploymentStateManager._load_state()
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
            DeploymentStateManager._save_state(state)
    
    @staticmethod
    def add_server_to_service(
        project: str,
        env: str,
        service: str,
        server_ip: str
    ):
        """Add a server to current service deployment"""
        state = DeploymentStateManager._load_state()
        
        try:
            current = state[project][env][service]["current"]
            if server_ip not in current["servers"]:
                current["servers"].append(server_ip)
                current["updated_at"] = datetime.now().isoformat()
                DeploymentStateManager._save_state(state)
                log(f"Added {server_ip} to {project}/{env}/{service}")
        except KeyError:
            log(f"Warning: Service {project}/{env}/{service} not found in deployment state")
    
    @staticmethod
    def get_services_on_server(server_ip: str) -> List[Dict[str, str]]:
        """Get all services currently deployed on a specific server"""
        state = DeploymentStateManager._load_state()
        services = []
        
        for project_name, project in state.items():
            for env_name, env in project.items():
                for service_name, service_data in env.items():
                    current = service_data.get("current")
                    if current and server_ip in current.get("servers", []):
                        services.append({
                            "project": project_name,
                            "env": env_name,
                            "service": service_name,
                            "container_name": current["container_name"],
                            "version": current.get("version", "unknown")
                        })
        
        return services