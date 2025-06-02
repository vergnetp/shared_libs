"""
Secret Management

Handles secret retrieval from multiple sources (Vault, OS environment)
and container secret creation for deployment across different platforms.
"""

import os
import subprocess
import json
import yaml
import tempfile
import base64
from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Any
from pathlib import Path


class VaultClient:
    """Simple Vault client for secret retrieval"""
    
    def __init__(self, vault_url: str = None, vault_token: str = None):
        self.vault_url = vault_url or os.getenv('VAULT_ADDR', 'http://localhost:8200')
        self.vault_token = vault_token or os.getenv('VAULT_TOKEN')
        self.available = self._check_availability()
    
    def _check_availability(self) -> bool:
        """Check if Vault is available and accessible"""
        if not self.vault_token:
            return False
        
        try:
            # Simple health check
            result = subprocess.run([
                'curl', '-s', '-H', f'X-Vault-Token: {self.vault_token}',
                f'{self.vault_url}/v1/sys/health'
            ], capture_output=True, timeout=5)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    
    def is_available(self) -> bool:
        """Check if Vault is available"""
        return self.available
    
    def get_secret(self, key: str) -> Optional[str]:
        """Get secret from Vault"""
        if not self.available:
            return None
        
        try:
            # Vault KV v2 format: secret/data/path
            vault_path = f"secret/data/{key.lower().replace('_', '/')}"
            
            result = subprocess.run([
                'curl', '-s', '-H', f'X-Vault-Token: {self.vault_token}',
                f'{self.vault_url}/v1/{vault_path}'
            ], capture_output=True, timeout=10)
            
            if result.returncode == 0:
                response = json.loads(result.stdout.decode())
                if 'data' in response and 'data' in response['data']:
                    return response['data']['data'].get('value')
        except (json.JSONDecodeError, subprocess.TimeoutExpired, KeyError):
            pass
        
        return None


class SecretManager:
    """
    Unified secret management with fallback hierarchy:
    1. Vault (if available)
    2. OS environment variables
    """
    
    def __init__(self, use_vault: bool = False):
        self.use_vault = use_vault
        self.vault_client = VaultClient() if use_vault else None
        
    def get_secret(self, key: str) -> Optional[str]:
        """Get secret from Vault or OS environment (fallback)"""
        
        # Try Vault first if enabled and available
        if self.use_vault and self.vault_client and self.vault_client.is_available():
            try:
                vault_secret = self.vault_client.get_secret(key)
                if vault_secret:
                    return vault_secret
            except Exception:
                pass  # Fall back to OS environment
        
        # Fallback to OS environment variables
        return os.getenv(key)
    
    def find_secret_value(self, secret_key: str, project: str, environment: str) -> Optional[str]:
        """Find secret value using multiple naming conventions"""
        
        # Try different environment variable naming patterns
        possible_env_names = [
            f"{project.upper()}_{environment.upper()}_{secret_key.upper()}",  # HOSTOMATIC_PROD_STRIPE_KEY
            f"{secret_key.upper()}",                                          # STRIPE_KEY (global)
            f"{project.upper()}_{secret_key.upper()}",                        # HOSTOMATIC_STRIPE_KEY
            f"{environment.upper()}_{secret_key.upper()}",                    # PROD_STRIPE_KEY
        ]
        
        for env_name in possible_env_names:
            value = self.get_secret(env_name)
            if value:
                return value
        
        return None
    
    def validate_required_secrets(self, project: str, environment: str, required_secrets: List[str]) -> Dict[str, bool]:
        """Validate that all required secrets are available"""
        validation_results = {}
        
        for secret_key in required_secrets:
            secret_value = self.find_secret_value(secret_key, project, environment)
            validation_results[secret_key] = secret_value is not None
        
        return validation_results
    
    def get_missing_secrets(self, project: str, environment: str, required_secrets: List[str]) -> List[str]:
        """Get list of missing secrets"""
        validation = self.validate_required_secrets(project, environment, required_secrets)
        return [secret for secret, available in validation.items() if not available]


# Abstract base class for container secret managers
class ContainerSecretHandler(ABC):
    """Abstract base class for container platform secret handlers"""
    
    def __init__(self, secret_manager: SecretManager):
        self.secret_manager = secret_manager
    
    @abstractmethod
    def create_secrets(self, project: str, environment: str, service_config: Dict[str, Any]) -> List[str]:
        """Create secrets for the platform"""
        pass
    
    @abstractmethod
    def remove_secret(self, secret_name: str, **kwargs) -> bool:
        """Remove a secret"""
        pass
    
    @abstractmethod
    def list_secrets(self, **kwargs) -> List[str]:
        """List all secrets"""
        pass
    
    @abstractmethod
    def cleanup_project_secrets(self, project: str, environment: str) -> int:
        """Remove all secrets for a project/environment"""
        pass
    
    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the platform name"""
        pass


class DockerSecretHandler(ContainerSecretHandler):
    """Docker-specific secret management"""
    
    def get_platform_name(self) -> str:
        return "docker"
    
    def create_secrets(self, project: str, environment: str, service_config: Dict[str, Any]) -> List[str]:
        """Create Docker secrets dynamically based on service configuration"""
        
        required_secrets = service_config.get('secrets', [])
        created_secrets = []
        
        for secret_key in required_secrets:
            secret_value = self.secret_manager.find_secret_value(secret_key, project, environment)
            
            if secret_value:
                docker_secret_name = f"{project}_{environment}_{secret_key}"
                
                if self._create_docker_secret(docker_secret_name, secret_value):
                    created_secrets.append(docker_secret_name)
                else:
                    print(f"Warning: Failed to create Docker secret {docker_secret_name}")
            else:
                print(f"Warning: Secret {secret_key} not found for {project}-{environment}")
        
        return created_secrets
    
    def _create_docker_secret(self, secret_name: str, secret_value: str) -> bool:
        """Create a Docker secret"""
        try:
            # Check if secret already exists
            check_result = subprocess.run([
                'docker', 'secret', 'inspect', secret_name
            ], capture_output=True)
            
            if check_result.returncode == 0:
                # Secret exists, remove it first (Docker secrets are immutable)
                subprocess.run(['docker', 'secret', 'rm', secret_name], capture_output=True)
            
            # Create new secret
            result = subprocess.run([
                'docker', 'secret', 'create', secret_name, '-'
            ], input=secret_value.encode(), capture_output=True)
            
            return result.returncode == 0
            
        except Exception as e:
            print(f"Error creating Docker secret {secret_name}: {e}")
            return False
    
    def remove_secret(self, secret_name: str, **kwargs) -> bool:
        """Remove a Docker secret"""
        try:
            result = subprocess.run([
                'docker', 'secret', 'rm', secret_name
            ], capture_output=True)
            return result.returncode == 0
        except Exception:
            return False
    
    def list_secrets(self, **kwargs) -> List[str]:
        """List all Docker secrets"""
        try:
            result = subprocess.run([
                'docker', 'secret', 'ls', '--format', '{{.Name}}'
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                return [line.strip() for line in result.stdout.split('\n') if line.strip()]
            
        except Exception:
            pass
        
        return []
    
    def cleanup_project_secrets(self, project: str, environment: str) -> int:
        """Remove all Docker secrets for a project/environment"""
        prefix = f"{project}_{environment}_"
        secrets = self.list_secrets()
        
        removed_count = 0
        for secret_name in secrets:
            if secret_name.startswith(prefix):
                if self.remove_secret(secret_name):
                    removed_count += 1
        
        return removed_count
    
    def get_project_secrets(self, project: str, environment: str) -> List[str]:
        """Get all Docker secrets for a project/environment"""
        prefix = f"{project}_{environment}_"
        secrets = self.list_secrets()
        return [secret for secret in secrets if secret.startswith(prefix)]


class KubernetesSecretHandler(ContainerSecretHandler):
    """Kubernetes-specific secret management"""
    
    def get_platform_name(self) -> str:
        return "kubernetes"
    
    def create_secrets(self, project: str, environment: str, service_config: Dict[str, Any]) -> List[str]:
        """Create Kubernetes secrets"""
        required_secrets = service_config.get('secrets', [])
        secret_name = f"{project}-{environment}-secrets"
        namespace = f"{project}-{environment}"
        
        # Gather all secret values
        secret_data = {}
        for secret_key in required_secrets:
            secret_value = self.secret_manager.find_secret_value(secret_key, project, environment)
            if secret_value:
                # Base64 encode for Kubernetes
                encoded_value = base64.b64encode(secret_value.encode()).decode()
                secret_data[secret_key] = encoded_value
        
        if secret_data:
            # Create Kubernetes secret manifest
            secret_manifest = {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": secret_name,
                    "namespace": namespace
                },
                "type": "Opaque",
                "data": secret_data
            }
            
            # Apply the secret
            try:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                    yaml.dump(secret_manifest, f)
                    temp_path = f.name
                
                # First ensure namespace exists
                self._ensure_namespace(namespace)
                
                # Apply the secret
                result = subprocess.run([
                    'kubectl', 'apply', '-f', temp_path
                ], capture_output=True, text=True)
                
                if result.returncode == 0:
                    print(f"Created Kubernetes secret: {secret_name}")
                    return [secret_name]
                else:
                    print(f"Failed to create Kubernetes secret: {result.stderr}")
                    return []
            except Exception as e:
                print(f"Error creating Kubernetes secret: {e}")
                return []
            finally:
                if 'temp_path' in locals():
                    os.unlink(temp_path)
        
        return []
    
    def _ensure_namespace(self, namespace: str) -> bool:
        """Ensure Kubernetes namespace exists"""
        try:
            # Check if namespace exists
            check_result = subprocess.run([
                'kubectl', 'get', 'namespace', namespace
            ], capture_output=True)
            
            if check_result.returncode != 0:
                # Create namespace
                result = subprocess.run([
                    'kubectl', 'create', 'namespace', namespace
                ], capture_output=True, text=True)
                
                if result.returncode == 0:
                    print(f"Created Kubernetes namespace: {namespace}")
                    return True
                else:
                    print(f"Failed to create namespace {namespace}: {result.stderr}")
                    return False
            
            return True
            
        except Exception as e:
            print(f"Error ensuring namespace {namespace}: {e}")
            return False
    
    def remove_secret(self, secret_name: str, namespace: str = None, **kwargs) -> bool:
        """Remove a Kubernetes secret"""
        try:
            cmd = ['kubectl', 'delete', 'secret', secret_name]
            if namespace:
                cmd.extend(['-n', namespace])
            
            result = subprocess.run(cmd, capture_output=True)
            return result.returncode == 0
        except Exception:
            return False
    
    def list_secrets(self, namespace: str = None, **kwargs) -> List[str]:
        """List all Kubernetes secrets"""
        try:
            cmd = ['kubectl', 'get', 'secrets', '--no-headers', '-o', 'custom-columns=:metadata.name']
            if namespace:
                cmd.extend(['-n', namespace])
            else:
                cmd.append('--all-namespaces')
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                return [line.strip() for line in result.stdout.split('\n') if line.strip()]
            
        except Exception:
            pass
        
        return []
    
    def cleanup_project_secrets(self, project: str, environment: str) -> int:
        """Remove all Kubernetes secrets for a project/environment"""
        namespace = f"{project}-{environment}"
        secret_name = f"{project}-{environment}-secrets"
        
        if self.remove_secret(secret_name, namespace=namespace):
            return 1
        return 0
    
    def get_project_secrets(self, project: str, environment: str) -> List[str]:
        """Get all Kubernetes secrets for a project/environment"""
        return [f"{project}-{environment}-secrets"]


class PodmanSecretHandler(ContainerSecretHandler):
    """Podman-specific secret management (using environment files)"""
    
    def get_platform_name(self) -> str:
        return "podman"
    
    def create_secrets(self, project: str, environment: str, service_config: Dict[str, Any]) -> List[str]:
        """Create Podman environment file for secrets"""
        required_secrets = service_config.get('secrets', [])
        env_file_path = f"/opt/app/{project}-{environment}.env"
        
        # Create environment file content
        env_content = []
        for secret_key in required_secrets:
            secret_value = self.secret_manager.find_secret_value(secret_key, project, environment)
            if secret_value:
                env_content.append(f"{secret_key.upper()}={secret_value}")
        
        if env_content:
            # For Podman, we return the path where the env file should be created
            # The actual file creation would happen during deployment
            print(f"Prepared environment file for Podman: {env_file_path}")
            return [env_file_path]
        
        return []
    
    def remove_secret(self, env_file_path: str, **kwargs) -> bool:
        """Remove Podman environment file"""
        try:
            if os.path.exists(env_file_path):
                os.remove(env_file_path)
                return True
            return True  # File doesn't exist, consider it removed
        except Exception:
            return False
    
    def list_secrets(self, **kwargs) -> List[str]:
        """List all Podman environment files"""
        try:
            env_files = []
            app_dir = Path('/opt/app')
            if app_dir.exists():
                for env_file in app_dir.glob('*.env'):
                    env_files.append(str(env_file))
            return env_files
        except Exception:
            return []
    
    def cleanup_project_secrets(self, project: str, environment: str) -> int:
        """Remove Podman environment file for a project/environment"""
        env_file_path = f"/opt/app/{project}-{environment}.env"
        
        if self.remove_secret(env_file_path):
            return 1
        return 0
    
    def get_project_secrets(self, project: str, environment: str) -> List[str]:
        """Get Podman environment file for a project/environment"""
        return [f"/opt/app/{project}-{environment}.env"]


class ContainerSecretManager:
    """
    Main container secret manager that delegates to platform-specific handlers
    """
    
    def __init__(self, secret_manager: SecretManager, platform: str = 'docker'):
        self.secret_manager = secret_manager
        self._handlers = {
            'docker': DockerSecretHandler(secret_manager),
            'kubernetes': KubernetesSecretHandler(secret_manager),
            'podman': PodmanSecretHandler(secret_manager)
        }
        self.set_platform(platform)
    
    def set_platform(self, platform: str):
        """Set the container platform for secret management"""
        if platform not in self._handlers:
            valid_platforms = list(self._handlers.keys())
            raise ValueError(f"Unsupported platform: {platform}. Valid platforms: {valid_platforms}")
        
        self.platform = platform
        self.handler = self._handlers[platform]
        print(f"Container secret manager set to {platform} platform")
    
    def get_platform(self) -> str:
        """Get current platform"""
        return self.platform
    
    def create_secrets(self, project: str, environment: str, service_config: Dict[str, Any]) -> List[str]:
        """Create secrets using the current platform handler"""
        return self.handler.create_secrets(project, environment, service_config)
    
    def remove_secret(self, secret_name: str, **kwargs) -> bool:
        """Remove a secret using the current platform handler"""
        return self.handler.remove_secret(secret_name, **kwargs)
    
    def list_secrets(self, **kwargs) -> List[str]:
        """List all secrets using the current platform handler"""
        return self.handler.list_secrets(**kwargs)
    
    def cleanup_project_secrets(self, project: str, environment: str) -> int:
        """Remove all secrets for a project/environment using the current platform handler"""
        return self.handler.cleanup_project_secrets(project, environment)
    
    def get_project_secrets(self, project: str, environment: str) -> List[str]:
        """Get all secrets for a project/environment using the current platform handler"""
        return self.handler.get_project_secrets(project, environment)
    
    def get_available_platforms(self) -> List[str]:
        """Get list of available platforms"""
        return list(self._handlers.keys())


def read_container_secret(secret_name: str, platform: str = 'docker') -> Optional[str]:
    """Helper function to read container secret from mounted location (for application code)"""
    try:
        if platform == 'docker':
            # Docker secrets are mounted as files in /run/secrets/
            secret_path = Path(f'/run/secrets/{secret_name}')
            if secret_path.exists():
                return secret_path.read_text().strip()
        elif platform == 'kubernetes':
            # Kubernetes secrets are typically exposed as environment variables
            return os.getenv(secret_name)
        elif platform == 'podman':
            # Podman secrets are typically in environment variables from env file
            return os.getenv(secret_name)
    except Exception:
        pass
    
    return None