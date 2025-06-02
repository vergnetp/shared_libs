"""
Docker platform implementation for container operations
"""

import subprocess
import tempfile
import os
import time
from typing import Dict, List, Any
from .base import ContainerRuntime, TemplateEngine, SecretHandler, PlatformCapabilities


class DockerRuntime(ContainerRuntime):
    """Docker container runtime implementation"""
    
    def __init__(self):
        self.capabilities = PlatformCapabilities("docker")
        self.capabilities.supports_secrets = True
        self.capabilities.supports_networking = True
        self.capabilities.supports_volumes = True
        self.capabilities.supports_health_checks = True
        self.capabilities.supports_rolling_updates = False
    
    def get_platform_name(self) -> str:
        return "docker"
    
    def build_image(self, image_name: str, containerfile_path: str, build_context: str, 
                   build_args: Dict[str, str] = None) -> bool:
        """Build Docker image"""
        try:
            cmd = [
                'docker', 'build',
                '-t', image_name,
                '-f', containerfile_path,
                build_context
            ]
            
            # Add build arguments if provided
            if build_args:
                for key, value in build_args.items():
                    cmd.extend(['--build-arg', f'{key}={value}'])
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            
            if result.returncode == 0:
                print(f"Successfully built Docker image: {image_name}")
                return True
            else:
                print(f"Docker build failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"Docker build timed out for image: {image_name}")
            return False
        except Exception as e:
            print(f"Error building Docker image: {e}")
            return False
    
    def deploy_service(self, config_file: str, working_dir: str = "/opt/app") -> bool:
        """Deploy service using docker-compose"""
        try:
            result = subprocess.run([
                'docker-compose', '-f', config_file, 'up', '-d'
            ], cwd=working_dir, capture_output=True, text=True, timeout=300)
            
            if result.returncode == 0:
                print(f"Successfully deployed Docker service from {config_file}")
                return True
            else:
                print(f"Docker deployment failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"Docker deployment timed out for {config_file}")
            return False
        except Exception as e:
            print(f"Error deploying Docker service: {e}")
            return False
    
    def check_service_status(self, service_name: str) -> str:
        """Check Docker container status"""
        try:
            result = subprocess.run([
                'docker', 'ps', '--filter', f'name={service_name}',
                '--format', '{{.Status}}'
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                output = result.stdout.strip()
                if not output:
                    return "not_found"
                elif "Up" in output:
                    return "running"
                else:
                    return "stopped"
            else:
                return "error"
                
        except Exception as e:
            print(f"Error checking Docker service status: {e}")
            return "error"
    
    def get_service_logs(self, service_name: str, lines: int = 100) -> str:
        """Get Docker container logs"""
        try:
            result = subprocess.run([
                'docker', 'logs', '--tail', str(lines), service_name
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Error getting logs: {result.stderr}"
                
        except Exception as e:
            return f"Error getting Docker logs: {e}"
    
    def stop_service(self, service_name: str) -> bool:
        """Stop Docker container"""
        try:
            result = subprocess.run([
                'docker', 'stop', service_name
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Successfully stopped Docker service: {service_name}")
                return True
            else:
                print(f"Failed to stop Docker service: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error stopping Docker service: {e}")
            return False
    
    def remove_service(self, service_name: str) -> bool:
        """Remove Docker container"""
        try:
            # Stop first, then remove
            self.stop_service(service_name)
            
            result = subprocess.run([
                'docker', 'rm', '-f', service_name
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Successfully removed Docker service: {service_name}")
                return True
            else:
                print(f"Failed to remove Docker service: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error removing Docker service: {e}")
            return False
    
    def restart_service(self, service_name: str) -> bool:
        """Restart Docker container"""
        try:
            result = subprocess.run([
                'docker', 'restart', service_name
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Successfully restarted Docker service: {service_name}")
                return True
            else:
                print(f"Failed to restart Docker service: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error restarting Docker service: {e}")
            return False
    
    def get_deploy_command(self, config_file: str) -> str:
        """Get Docker deployment command"""
        return f"cd /opt/app && docker-compose -f {os.path.basename(config_file)} up -d"


class DockerTemplateEngine(TemplateEngine):
    """Docker Compose template engine"""
    
    def get_platform_name(self) -> str:
        return "docker"
    
    def get_config_file_extension(self) -> str:
        return "yml"
    
    def supports_secrets(self) -> bool:
        return True
    
    def supports_networking(self) -> bool:
        return True
    
    def generate_deployment_config(self, context: Dict[str, Any]) -> str:
        """Generate Docker Compose configuration"""
        
        service_name = context['service_name']
        image_name = context['image_name']
        is_worker = context.get('is_worker', False)
        replica_count = context.get('replica_count', 1)
        
        config = f"""version: '3.8'
services:
  {service_name}:
    image: {image_name}
    container_name: {service_name}"""
        
        # Add command for workers
        if context.get('command'):
            config += f"\n    command: {context['command']}"
        
        # Add environment variables
        config += "\n    environment:"
        env_vars = [
            'DB_USER', 'DB_NAME', 'DB_HOST', 'DB_PORT', 
            'REDIS_HOST', 'REDIS_PORT',
            'VAULT_HOST', 'VAULT_PORT', 
            'OPENSEARCH_HOST', 'OPENSEARCH_PORT', 'OPENSEARCH_INDEX',
            'SERVICE_NAME', 'ENVIRONMENT', 'PROJECT', 'RESOURCE_HASH'
        ]
        
        for var in env_vars:
            if var in context:
                config += f"\n      - {var}={context[var]}"
        
        # Add SERVICE_PORT for web services only
        if not is_worker and 'SERVICE_PORT' in context:
            config += f"\n      - SERVICE_PORT={context['SERVICE_PORT']}"
        
        # Add secrets configuration
        secrets_config = context.get('secrets_config', {})
        if secrets_config.get('type') == 'docker_secrets':
            config += "\n    secrets:"
            for secret in secrets_config.get('secrets', []):
                config += f"\n      - {secret}"
        elif secrets_config.get('type') == 'env_file':
            config += f"\n    env_file:\n      - {secrets_config.get('env_file')}"
        
        # Add ports for web services
        if not is_worker and 'SERVICE_PORT' in context:
            port = context['SERVICE_PORT']
            config += f"\n    ports:\n      - \"{port}:{port}\""
        
        # Add volumes if specified
        if context.get('volumes'):
            config += "\n    volumes:"
            for volume in context['volumes']:
                config += f"\n      - {volume}"
        
        # Add restart policy
        config += "\n    restart: unless-stopped"
        
        # Add networks
        config += "\n    networks:\n      - app-network"
        
        # Add health check for web services
        if not is_worker and 'SERVICE_PORT' in context:
            port = context['SERVICE_PORT']
            config += f"""
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{port}/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s"""
        
        # Add resource limits if specified
        if context.get('resources'):
            resources = context['resources']
            config += "\n    deploy:"
            config += "\n      resources:"
            
            if 'limits' in resources:
                config += "\n        limits:"
                for key, value in resources['limits'].items():
                    config += f"\n          {key}: {value}"
            
            if 'reservations' in resources:
                config += "\n        reservations:"
                for key, value in resources['reservations'].items():
                    config += f"\n          {key}: {value}"
        
        # Add scale configuration for replicas
        if replica_count > 1:
            config += f"""
    deploy:
      replicas: {replica_count}
      restart_policy:
        condition: on-failure
        delay: 5s
        max_attempts: 3"""
        
        # Add networks section
        config += "\n\nnetworks:\n  app-network:\n    driver: bridge"
        
        # Add secrets section for Docker secrets
        if secrets_config.get('type') == 'docker_secrets':
            config += "\n\nsecrets:"
            for secret in secrets_config.get('secrets', []):
                config += f"\n  {secret}:\n    external: true"
        
        # Add volumes section if needed
        if context.get('named_volumes'):
            config += "\n\nvolumes:"
            for volume in context['named_volumes']:
                config += f"\n  {volume}:\n    driver: local"
        
        return config
    
    def get_health_check_url(self, service_name: str, host: str, port: int) -> str:
        """Generate health check URL for Docker service"""
        return f"http://{host}:{port}/health"


class DockerSecretHandler(SecretHandler):
    """Docker secret handler implementation"""
    
    def __init__(self, secret_manager):
        self.secret_manager = secret_manager
    
    def get_platform_name(self) -> str:
        return "docker"
    
    def create_secrets(self, project: str, environment: str, secrets: Dict[str, str]) -> List[str]:
        """Create Docker secrets"""
        created_secrets = []
        
        for secret_key, secret_value in secrets.items():
            docker_secret_name = f"{project}_{environment}_{secret_key}"
            
            if self._create_docker_secret(docker_secret_name, secret_value):
                created_secrets.append(docker_secret_name)
                print(f"Created Docker secret: {docker_secret_name}")
            else:
                print(f"Warning: Failed to create Docker secret {docker_secret_name}")
        
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
                print(f"Updating existing Docker secret: {secret_name}")
                subprocess.run(['docker', 'secret', 'rm', secret_name], capture_output=True)
            
            # Create new secret
            result = subprocess.run([
                'docker', 'secret', 'create', secret_name, '-'
            ], input=secret_value.encode(), capture_output=True, text=True)
            
            if result.returncode == 0:
                return True
            else:
                print(f"Failed to create Docker secret {secret_name}: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error creating Docker secret {secret_name}: {e}")
            return False
    
    def remove_secret(self, secret_name: str, **kwargs) -> bool:
        """Remove a Docker secret"""
        try:
            result = subprocess.run([
                'docker', 'secret', 'rm', secret_name
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                print(f"Removed Docker secret: {secret_name}")
                return True
            else:
                print(f"Failed to remove Docker secret {secret_name}: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"Error removing Docker secret {secret_name}: {e}")
            return False
    
    def list_secrets(self, **kwargs) -> List[str]:
        """List all Docker secrets"""
        try:
            result = subprocess.run([
                'docker', 'secret', 'ls', '--format', '{{.Name}}'
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                return [line.strip() for line in result.stdout.split('\n') if line.strip()]
            else:
                print(f"Failed to list Docker secrets: {result.stderr}")
                return []
                
        except Exception as e:
            print(f"Error listing Docker secrets: {e}")
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
        
        if removed_count > 0:
            print(f"Cleaned up {removed_count} Docker secrets for {project}-{environment}")
        
        return removed_count
    
    def get_project_secrets(self, project: str, environment: str) -> List[str]:
        """Get all Docker secrets for a project/environment"""
        prefix = f"{project}_{environment}_"
        secrets = self.list_secrets()
        return [secret for secret in secrets if secret.startswith(prefix)]
    
    def validate_secret_availability(self, secret_name: str) -> bool:
        """Validate that a Docker secret exists and is accessible"""
        try:
            result = subprocess.run([
                'docker', 'secret', 'inspect', secret_name
            ], capture_output=True)
            
            return result.returncode == 0
            
        except Exception:
            return False