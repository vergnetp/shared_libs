"""
Environment Variable Generation

Generates dynamic environment variables for services based on project,
environment, and hash-based resource allocation.
"""

import hashlib
from typing import Dict, List, Any
from .infrastructure_state import InfrastructureState
from .managers.secret_manager import ContainerSecretManager


class EnvironmentGenerator:
    """
    Generates dynamic environment variables for service deployment
    """
    
    def __init__(self, infrastructure_state: InfrastructureState, container_secret_manager: ContainerSecretManager):
        self.state = infrastructure_state
        self.container_secret_manager = container_secret_manager
        
    def generate_dynamic_environment(self, project: str, environment: str, 
                                   service_type: str, service_config: Dict[str, Any]) -> Dict[str, str]:
        """Generate dynamic environment variables using hashes and Container secrets"""
        
        # Generate hash for deterministic resource naming
        resource_hash = self.state.generate_resource_hash(project, environment)
        
        # Create Container secrets for sensitive data
        self.container_secret_manager.create_container_secrets(project, environment, service_config)
        
        # Get droplet/infrastructure info
        assigned_droplets = service_config.get("assigned_droplets", [])
        
        dynamic_vars = {
            # Database configuration using hashes
            "DB_USER": f"user_{resource_hash[:8]}",
            "DB_NAME": f"{project}_{environment}_{resource_hash[:8]}",
            "DB_HOST": self._get_database_host(project, environment),
            "DB_PORT": str(self.state.get_hash_based_port(project, environment, 5000, 1000)),
            
            # Redis configuration
            "REDIS_HOST": self._get_redis_host(project, environment),
            "REDIS_PORT": str(self.state.get_hash_based_port(project, environment, 6000, 1000)),
            
            # Vault configuration (project-specific)
            "VAULT_HOST": self._get_vault_host(project, environment),
            "VAULT_PORT": str(self.state.get_hash_based_port(project, environment, 8000, 1000)),
            
            # OpenSearch configuration (project-specific)
            "OPENSEARCH_HOST": self._get_opensearch_host(project, environment),
            "OPENSEARCH_PORT": str(self.state.get_hash_based_port(project, environment, 9000, 1000)),
            "OPENSEARCH_INDEX": f"{project}-{environment}-logs-{resource_hash[:6]}",
            
            # Service-specific configuration
            "SERVICE_NAME": f"{project}-{environment}-{service_type}",
            "ENVIRONMENT": environment,
            "PROJECT": project,
            
            # Infrastructure info
            "ASSIGNED_DROPLETS": ",".join(assigned_droplets),
            "RESOURCE_HASH": resource_hash
        }
        
        # Add port only for web services, not workers
        if service_config.get('type') != 'worker':
            if service_type == "backend":
                dynamic_vars["SERVICE_PORT"] = str(self.state.get_hash_based_port(project, environment, 8000, 1000))
            elif service_type == "frontend":
                dynamic_vars["SERVICE_PORT"] = str(self.state.get_hash_based_port(project, environment, 9000, 1000))
            else:
                # Custom service types
                dynamic_vars["SERVICE_PORT"] = str(self.state.get_hash_based_port(project, environment, 8000, 1000))
        
        return dynamic_vars
    
    def _get_database_host(self, project: str, environment: str) -> str:
        """Get database host for project/environment"""
        # Find where the database service is running
        project_services = self.state.get_project_services(f"{project}-{environment}")
        database_config = project_services.get("database", {})
        
        assigned_droplets = database_config.get("assigned_droplets", [])
        if assigned_droplets:
            # Get IP of first assigned droplet
            droplet = self.state.get_droplet(assigned_droplets[0])
            if droplet:
                return droplet["ip"]
        
        # Fallback to master droplet
        master = self.state.get_master_droplet()
        return master["ip"] if master else "localhost"
    
    def _get_redis_host(self, project: str, environment: str) -> str:
        """Get Redis host for project/environment"""
        # Find where the Redis service is running
        project_services = self.state.get_project_services(f"{project}-{environment}")
        redis_config = project_services.get("redis", {})
        
        assigned_droplets = redis_config.get("assigned_droplets", [])
        if assigned_droplets:
            # Get IP of first assigned droplet
            droplet = self.state.get_droplet(assigned_droplets[0])
            if droplet:
                return droplet["ip"]
        
        # Fallback to master droplet
        master = self.state.get_master_droplet()
        return master["ip"] if master else "localhost"
    
    def _get_vault_host(self, project: str, environment: str) -> str:
        """Get Vault host for project/environment (usually master)"""
        # Project-specific Vault instances run on master
        master = self.state.get_master_droplet()
        return master["ip"] if master else "localhost"
    
    def _get_opensearch_host(self, project: str, environment: str) -> str:
        """Get OpenSearch host for project/environment (usually master)"""
        # Project-specific OpenSearch instances run on master
        master = self.state.get_master_droplet()
        return master["ip"] if master else "localhost"
    
    def generate_docker_secrets_list(self, project: str, environment: str, service_config: Dict[str, Any]) -> List[str]:
        """Generate list of Docker secret names for a service"""
        required_secrets = service_config.get('secrets', [])
        
        docker_secret_names = []
        for secret_key in required_secrets:
            docker_secret_name = f"{project}_{environment}_{secret_key}"
            docker_secret_names.append(docker_secret_name)
        
        return docker_secret_names
    
    def generate_template_context(self, project: str, environment: str, service_type: str, 
                                 service_config: Dict[str, Any], image_name: str) -> Dict[str, Any]:
        """Generate complete template context for service deployment"""
        
        # Get dynamic environment variables
        env_vars = self.generate_dynamic_environment(project, environment, service_type, service_config)
        
        # Get Docker secrets list
        secrets = self.generate_docker_secrets_list(project, environment, service_config)
        
        # Generate service name
        service_name = self.state.get_service_name(f"{project}-{environment}", service_type)
        
        template_context = {
            # Basic service info
            "service_name": service_name,
            "image_name": image_name,
            "project": project,
            "environment": environment,
            "service_type": service_type,
            
            # Service configuration
            "is_worker": service_config.get('type') == 'worker',
            "command": service_config.get('command'),
            "replica_count": service_config.get('replicas', 1),
            
            # Environment variables (non-sensitive)
            **env_vars,
            
            # Docker secrets (sensitive)
            "secrets": secrets,
            
            # Template helpers
            "secret_values": self._get_secret_values_for_k8s(project, environment, service_config)
        }
        
        return template_context
    
    def _get_secret_values_for_k8s(self, project: str, environment: str, service_config: Dict[str, Any]) -> Dict[str, str]:
        """Get base64-encoded secret values for Kubernetes templates"""
        import base64
        
        secret_values = {}
        required_secrets = service_config.get('secrets', [])
        
        for secret_key in required_secrets:
            secret_value = self.container_secret_manager.secret_manager.find_secret_value(
                secret_key, project, environment
            )
            if secret_value:
                # Base64 encode for Kubernetes
                encoded_value = base64.b64encode(secret_value.encode()).decode()
                secret_values[secret_key] = encoded_value
        
        return secret_values
    
    def validate_environment_config(self, project: str, environment: str, 
                                  service_type: str, service_config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate environment configuration for a service"""
        
        issues = []
        warnings = []
        
        # Check required secrets (skip for master services as they don't need app secrets)
        if service_config.get("type") != "master":
            required_secrets = service_config.get('secrets', [])
            missing_secrets = self.container_secret_manager.secret_manager.get_missing_secrets(
                project, environment, required_secrets
            )
            
            if missing_secrets:
                issues.append(f"Missing secrets: {', '.join(missing_secrets)}")
        
        # Check assigned droplets exist
        assigned_droplets = service_config.get('assigned_droplets', [])
        for droplet_name in assigned_droplets:
            if not self.state.get_droplet(droplet_name):
                issues.append(f"Assigned droplet {droplet_name} does not exist")
        
        # Check for port conflicts (web services only, not workers or master)
        service_type_info = service_config.get('type', 'web')
        if service_type_info == 'web' and 'port' in service_config:
            port = service_config['port']
            
            # Check if port is already used by another service on same droplets
            for droplet_name in assigned_droplets:
                other_services = self.state.get_services_on_droplet(droplet_name)
                for other_service in other_services:
                    current_service_name = self.state.get_service_name(f"{project}-{environment}", service_type)
                    if other_service['service_name'] != service_type and other_service.get('config', {}).get('port') == port:
                        issues.append(f"Port {port} conflict with {other_service['service_name']} on droplet {droplet_name}")
        
        # Check database/redis connectivity (skip for master services)
        if service_type_info != "master":
            required_secrets = service_config.get('secrets', [])
            if any('database' in s.lower() or 'db' in s.lower() for s in required_secrets):
                db_host = self._get_database_host(project, environment)
                if db_host == "localhost":
                    warnings.append("Database host defaulting to localhost - check database service deployment")
            
            if any('redis' in s.lower() for s in required_secrets):
                redis_host = self._get_redis_host(project, environment)
                if redis_host == "localhost":
                    warnings.append("Redis host defaulting to localhost - check Redis service deployment")
        
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "missing_secrets": self.container_secret_manager.secret_manager.get_missing_secrets(
                project, environment, service_config.get('secrets', [])
            ) if service_config.get("type") != "master" else []
        }
    
    def get_service_discovery_info(self, project: str, environment: str) -> Dict[str, Dict[str, Any]]:
        """Get service discovery information for all services in a project/environment"""
        
        services = self.state.get_project_services(project, environment)
        
        discovery_info = {}
        
        for service_type, service_config in services.items():
            if service_type == "workers":
                # Handle workers array
                if isinstance(service_config, list):
                    for i, worker_config in enumerate(service_config):
                        worker_name = f"worker_{i}"
                        endpoints = []
                        assigned_droplets = worker_config.get("assigned_droplets", [])
                        
                        for droplet_name in assigned_droplets:
                            droplet = self.state.get_droplet(droplet_name)
                            if droplet:
                                endpoint_info = {
                                    "droplet": droplet_name,
                                    "ip": droplet["ip"],
                                    "command": worker_config.get("command")
                                }
                                endpoints.append(endpoint_info)
                        
                        discovery_info[worker_name] = {
                            "type": "worker",
                            "endpoints": endpoints,
                            "load_balanced": False,  # Workers are not load balanced
                            "health_check_url": None  # Workers don't have HTTP health checks
                        }
            else:
                # Handle regular services
                service_name = self.state.get_service_name(f"{project}-{environment}", service_type)
                
                # Get service endpoints
                endpoints = []
                assigned_droplets = service_config.get("assigned_droplets", [])
                
                for droplet_name in assigned_droplets:
                    droplet = self.state.get_droplet(droplet_name)
                    if droplet:
                        endpoint_info = {
                            "droplet": droplet_name,
                            "ip": droplet["ip"]
                        }
                        
                        # Add port for web services
                        if 'port' in service_config:
                            endpoint_info["port"] = service_config['port']
                            endpoint_info["url"] = f"http://{droplet['ip']}:{service_config['port']}"
                        
                        endpoints.append(endpoint_info)
                
                service_type_info = service_config.get("type", "web")
                discovery_info[service_name] = {
                    "type": service_type_info,
                    "endpoints": endpoints,
                    "load_balanced": len(endpoints) > 1 and service_type_info == "web",
                    "health_check_url": f"http://{endpoints[0]['ip']}:{service_config.get('port', 8080)}/health" if endpoints and service_type_info == "web" and 'port' in service_config else None
                }
        
        return discovery_info
