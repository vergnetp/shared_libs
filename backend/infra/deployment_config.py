import json
from typing import Dict, Any, List
import constants
from deployment_syncer import DeploymentSyncer
import secrets
import string
import hashlib
from pathlib import Path

import copy

def replace_env(obj: Any, env: str) -> Any:
    """Recursively replace '{env}' in strings, dict keys, and values."""
    if isinstance(obj, str):
        return obj.replace("{env}", env)
    if isinstance(obj, list):
        return [replace_env(v, env) for v in obj]
    if isinstance(obj, dict):
        return {
            (k.replace("{env}", env) if isinstance(k, str) else k): replace_env(v, env)
            for k, v in obj.items()
        }
    return obj

def merge_dicts(base: Dict[str, Any], overrides: Dict[str, Any], env: str) -> Dict[str, Any]:
    """Deep merge with overrides taking precedence. Special handling for dockerfile_content."""
    base = replace_env(copy.deepcopy(base), env)
    overrides = replace_env(copy.deepcopy(overrides), env)

    result = copy.deepcopy(base)
    for k, v in overrides.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            if k == "dockerfile_content":
                # For dockerfile_content, merge the numbered keys
                result[k] = {**result[k], **v}
            else:
                result[k] = merge_dicts(result[k], v, env)
        else:
            result[k] = v
    return result

def post_process_dockerfile_content(services: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Post-process merged services to handle dockerfile_content properly"""
    
    for service_name, service_config in services.items():
        if "dockerfile_content" in service_config:
            dockerfile_content = service_config["dockerfile_content"]
            
            # Sort the dockerfile_content keys properly
            def sort_key(key):
                parts = key.split('.')
                return [int(part) for part in parts]
            
            try:
                sorted_keys = sorted(dockerfile_content.keys(), key=sort_key)
                # Rebuild dockerfile_content in sorted order (as ordered dict)
                sorted_dockerfile_content = {}
                for key in sorted_keys:
                    sorted_dockerfile_content[key] = dockerfile_content[key]
                
                service_config["dockerfile_content"] = sorted_dockerfile_content
                
            except ValueError:
                print(f"Warning: Non-numeric keys in dockerfile_content for {service_name}")
    
    return services

def prepare_raw_config(config):
    """Add volumes to base services and provision standard services"""
    pass

def provision_standard_service(project: str, env: str, service: str, existing_config: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-generate configuration for standard services, respecting existing config"""
    from deployment_syncer import DeploymentSyncer
    
    local_base = DeploymentSyncer.get_local_base(project, env)
    safe_chars = string.ascii_letters + string.digits  # Only safe ASCII characters
    
    def merge_config(default_config: Dict[str, Any]) -> Dict[str, Any]:
        """Helper to merge default config with existing config"""
        result = {}
        for key, value in default_config.items():
            if key in existing_config:
                if key == "env_vars" and isinstance(value, dict) and isinstance(existing_config[key], dict):
                    # Merge env_vars, existing takes precedence
                    result[key] = {**value, **existing_config[key]}
                else:
                    # Use existing value
                    result[key] = existing_config[key]
            else:
                # Use default value
                result[key] = value
        
        # Add any other keys from existing config
        for key, value in existing_config.items():
            if key not in result:
                result[key] = value
        
        return result
    
    if service == "postgres":
        # Generate secure password only if not already exists
        secrets_dir = Path(local_base) / "secrets" / "postgres"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        password_file = secrets_dir / "db_password"
        
        if not password_file.exists():
            password = ''.join(secrets.choice(safe_chars) for _ in range(32))
            password_file.write_text(password, encoding='utf-8')
        
        # Generate database name and user from project/env
        hash_input = f"{project}_{env}_postgres"
        db_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]
        
        default_config = {
            "image": "postgres:15",
            "env_vars": {
                "POSTGRES_DB": f"{project}_{db_suffix}",
                "POSTGRES_USER": f"{project}_user",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/db_password"
            },
            "startup_order": 1
        }
        
        result = merge_config(default_config)
        print(f"Provisioned PostgreSQL for {project}/{env}: image={result['image']}, startup_order={result['startup_order']}")
        return result
    
    elif service == "redis":
        # Generate Redis auth password
        secrets_dir = Path(local_base) / "secrets" / "redis"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        password_file = secrets_dir / "redis_password"
        
        if not password_file.exists():
            password = ''.join(secrets.choice(safe_chars) for _ in range(32))
            password_file.write_text(password, encoding='utf-8')
        
        default_config = {
            "image": "redis:7-alpine",
            "command": ["redis-server", "--requirepass", "$(cat /run/secrets/redis_password)"],
            "startup_order": 1
        }
        
        result = merge_config(default_config)
        print(f"Provisioned Redis for {project}/{env}: image={result['image']}, startup_order={result['startup_order']}")
        return result
    
    elif service == "opensearch":
        # Generate admin password
        secrets_dir = Path(local_base) / "secrets" / "opensearch"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        password_file = secrets_dir / "admin_password"
        
        if not password_file.exists():
            password = ''.join(secrets.choice(safe_chars) for _ in range(32))
            password_file.write_text(password, encoding='utf-8')
        
        default_config = {
            "image": "opensearchproject/opensearch:2",
            "env_vars": {
                "discovery.type": "single-node",
                "OPENSEARCH_INITIAL_ADMIN_PASSWORD": "$(cat /run/secrets/admin_password)"
            },
            "startup_order": 1
        }
        
        result = merge_config(default_config)
        print(f"Provisioned OpenSearch for {project}/{env}: image={result['image']}, startup_order={result['startup_order']}")
        return result
    
    elif service == "nginx":
        # Generate basic auth password if needed
        secrets_dir = Path(local_base) / "secrets" / "nginx"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        
        default_config = {
            "image": "nginx:alpine",
            "startup_order": 10  # Usually starts after backend services
        }
        
        result = merge_config(default_config)
        print(f"Provisioned Nginx for {project}/{env}: image={result['image']}, startup_order={result['startup_order']}")
        return result
    
    else:
        # Unknown service, return as-is
        return existing_config

class DeploymentConfigurer:
    """
    Manage deployment configuration for multiple environments.

    Attributes:
        - raw_config (dict): The original JSON config loaded from disk. Editable.
        - config (dict): Derived config per environment, ready for deployment.
    
    Public methods:
        - save_config(): Save raw_config to disk.
        - validate_config(): Validate required fields exist.
        - rebuild_config(): Rebuild derived config from raw_config.
        - save_final_config(): Save the fully processed config for audit/debug.

    Behavior:
        - Base services are defined under `project.services`.
        - Environments (dev, test, uat, production) are optional in raw_config.
          They will always exist in self.config.
        - Each environment's services are merged with base services.
        - Standard services are auto-provisioned during config build.
        - self.config contains no top-level `services`; all services live under each environment.

    Config file location:
        - Default: <deployment_config_path>/deploy-config.json
          where `deployment_config_path` is returned by `constants.get_deployment_config_path()`.
        - Can specify a custom filename via `config_file` argument.
        - Folder is created automatically if missing when saving.

    Minimum viable raw configuration:

    {
        "project": {
            "name": "<project_name>",
            "docker_hub_user": "<docker_hub_username>",
            "services": {
                "postgres": {},
                "web": {"image": "my-web:latest", "ports": [80]}
            }
            // "environments" key is optional
        }
    }

    Derived config (self.config) after rebuild_config:

    {
        "project": {
            "name": "<project_name>",
            "docker_hub_user": "<docker_hub_username>",
            "environments": {
                "dev": { "services": { ...merged and provisioned services... } },
                "test": { "services": { ...merged and provisioned services... } },
                "uat": { "services": { ...merged and provisioned services... } },
                "production": { "services": { ...merged and provisioned services... } }
            }
        }
    }
    """

    DEFAULT_ENVS = ["dev", "test", "uat", "production"]

    def __init__(self, project_name: str):
        """
        Initialize deployment configuration for a specific project.
        
        Args:
            project_name: Name of the project (loads config/projects/<project_name>.json)
        
        Raises:
            FileNotFoundError: If project config file not found
            ValueError: If project_name not specified
        """
        if not project_name:
            projects = constants.list_projects()
            if projects:
                raise ValueError(
                    f"Must specify project_name. Available projects: {', '.join(projects)}"
                )
            else:
                raise ValueError("No projects found in config/projects/")
        
        self.project_name = project_name
        self.config_file = constants.get_project_config_path(project_name)
        
        if not self.config_file.exists():
            available = constants.list_projects()
            if available:
                raise FileNotFoundError(
                    f"Project '{project_name}' not found. Available: {', '.join(available)}"
                )
            else:
                raise FileNotFoundError(
                    f"Project '{project_name}' not found. No projects in config/projects/"
                )
        
        self.raw_config = self._load_raw_config()
        prepare_raw_config(self.raw_config)
        self.rebuild_config()
        self.validate_config()

    def _load_raw_config(self) -> Dict[str, Any]:
        """Load raw JSON configuration from file."""
        if not self.config_file.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_file}")
        try:
            with self.config_file.open("r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {self.config_file}: {e}")

    @staticmethod
    def list_projects() -> List[str]:
        """List all available projects."""
        return constants.list_projects()
    
    def rebuild_config(self):
        """
        Build derived config (self.config) from self.raw_config.

        - Merges base project.services into each environment.
        - Ensures all default environments exist.
        - Auto-provisions standard services (postgres, redis, etc.)
        """
        project = self.raw_config.get("project", {})
        base_services = project.get("services", {})
        raw_envs = project.get("environments", {})

        merged_environments = {}
        for env_name in self.DEFAULT_ENVS:
            env_services = raw_envs.get(env_name, {}).get("services", {})
            merged_services = merge_dicts(base_services.copy(), env_services, env_name)        
            merged_services = post_process_dockerfile_content(merged_services)
            
            # Auto-provision standard services
            for service_name, service_config in merged_services.items():
                if not service_config.get("dockerfile") and not service_config.get("dockerfile_content"):
                    # Check if this is a standard service that needs provisioning
                    if service_name in ["postgres", "redis", "opensearch", "nginx"]:
                        print(f"Auto-provisioning standard service: {service_name} for {env_name}")
                        provisioned_config = provision_standard_service(
                            project.get("name"), env_name, service_name, service_config
                        )
                        merged_services[service_name] = provisioned_config
            
            merged_environments[env_name] = {"services": merged_services}

        self.config = {
            "project": {
                **{k: v for k, v in project.items() if k != "environments"},
                "environments": merged_environments
            }
        }
        self.config.get('project', {}).pop('services', None)
        

    def save_config(self):
        """
        Save the current raw_config to JSON file.

        - Folder is created automatically if missing.
        - Overwrites existing file.
        """
        self.validate_config()
        try:
            self.config_file.parent.mkdir(exist_ok=True, parents=True)
            with self.config_file.open("w") as f:
                json.dump(self.raw_config, f, indent=4)
        except Exception as e:
            raise Exception(f"Cannot save deployer config: {e}")

    def save_final_config(self, deployment_id: str):
        """
        Save the final processed config for audit/debug purposes.
        
        Args:
            deployment_id (str): Unique deployment ID for the config file
        """
        try:
            config_path = constants.get_deployment_files_path(deployment_id) / 'final_config.json'
            config_path.parent.mkdir(exist_ok=True, parents=True)
            with config_path.open("w") as f:
                json.dump(self.config, f, indent=4)
            print(f"Saved final config to: {config_path}")
        except Exception as e:
            print(f"Warning: Could not save final config: {e}")

    def validate_config(self):
        """
        Validate required fields exist in raw_config.

        Required fields:
            - project.name
            - project.docker_hub_user
            - project.services (can be empty)
        """
        project = self.raw_config.get("project", {})
        if "name" not in project:
            raise ValueError("Missing required field: project.name")
        if "docker_hub_user" not in project:
            raise ValueError("Missing required field: project.docker_hub_user")
        if "services" not in project:
            raise ValueError("Missing required field: project.services")

    def get_project_name(self) -> str:
        return self.config["project"]["name"]
    
    def get_docker_hub_user(self) -> str:
        return self.config["project"]["docker_hub_user"]
    
    def get_version(self) -> str:
        return self.config["project"].get("version", "latest")

    def get_default_server_ip(self) -> str:
        """Get default server IP from project configuration"""
        return self.config.get("project", {}).get("default_server_ip", 'localhost')
    
    def get_environments(self) -> list[str]:
        """Return a list of all environment names"""
        return list(self.config['project']['environments'].keys())

    def get_services(self, env: str) -> dict[str, dict]:
        """Return all services for a given environment"""
        return self.config['project']['environments'].get(env, {}).get("services", {})