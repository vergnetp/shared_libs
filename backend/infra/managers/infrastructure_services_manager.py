"""
Infrastructure Services Manager

Manages deployment of core infrastructure services like PostgreSQL, Redis,
OpenSearch, and Vault across the infrastructure.
"""

import json
import tempfile
import os
from typing import Dict, List, Any, Optional
from pathlib import Path

from ..infrastructure_state import InfrastructureState
from .ssh_key_manager import SSHKeyManager
from .secret_manager import SecretManager


class InfrastructureServicesManager:
    """
    Manages core infrastructure services deployment and configuration
    """
    
    def __init__(self, infrastructure_state: InfrastructureState, 
                 ssh_manager: SSHKeyManager, secret_manager: SecretManager):
        self.state = infrastructure_state
        self.ssh_manager = ssh_manager
        self.secret_manager = secret_manager
        
        # Service definitions
        self.service_definitions = {
            'postgresql': {
                'image': 'postgres:15-alpine',
                'port': 5432,
                'environment': {
                    'POSTGRES_DB': '{project}_{environment}',
                    'POSTGRES_USER': 'user_{resource_hash}',
                    'POSTGRES_PASSWORD': '{db_password}',
                    'POSTGRES_INITDB_ARGS': '--auth-host=scram-sha-256'
                },
                'volumes': ['/var/lib/postgresql/data'],
                'health_check': 'pg_isready -U {user}'
            },
            'redis': {
                'image': 'redis:7-alpine',
                'port': 6379,
                'command': 'redis-server --requirepass {redis_password}',
                'volumes': ['/data'],
                'health_check': 'redis-cli ping'
            },
            'opensearch': {
                'image': 'opensearchproject/opensearch:2.8.0',
                'port': 9200,
                'environment': {
                    'discovery.type': 'single-node',
                    'OPENSEARCH_JAVA_OPTS': '-Xms512m -Xmx512m',
                    'OPENSEARCH_INITIAL_ADMIN_PASSWORD': '{opensearch_admin_password}'
                },
                'volumes': ['/usr/share/opensearch/data'],
                'health_check': 'curl -f http://localhost:9200/_cluster/health'
            },
            'vault': {
                'image': 'vault:1.13.3',
                'port': 8200,
                'environment': {
                    'VAULT_DEV_ROOT_TOKEN_ID': '{vault_root_token}',
                    'VAULT_DEV_LISTEN_ADDRESS': '0.0.0.0:8200'
                },
                'volumes': ['/vault/data', '/vault/config'],
                'health_check': 'vault status'
            }
        }
    
    def deploy_infrastructure_services(self, project: str, environment: str) -> Dict[str, Any]:
        """Deploy all infrastructure services for a project/environment"""
        
        results = {}
        overall_success = True
        
        # Get project configuration from deployment config
        project_key = f"{project}-{environment}"
        project_services = self.state.get_project_services(project_key)
        
        # Deploy each required infrastructure service
        for service_name in ['postgresql', 'redis', 'opensearch', 'vault']:
            if service_name in project_services:
                print(f"Deploying infrastructure service: {service_name}")
                
                try:
                    result = self.deploy_service(project, environment, service_name)
                    results[service_name] = result
                    
                    if not result.get('success', False):
                        overall_success = False
                        
                except Exception as e:
                    results[service_name] = {
                        'success': False,
                        'error': str(e)
                    }
                    overall_success = False
        
        return {
            'success': overall_success,
            'services': results,
            'project': project,
            'environment': environment
        }
    
    def deploy_service(self, project: str, environment: str, service_name: str) -> Dict[str, Any]:
        """Deploy a specific infrastructure service"""
        
        if service_name not in self.service_definitions:
            return {
                'success': False,
                'error': f'Unknown infrastructure service: {service_name}'
            }
        
        service_def = self.service_definitions[service_name]
        
        # Get service configuration from state
        project_key = f"{project}-{environment}"
        service_config = self.state.get_project_services(project_key).get(service_name, {})
        assigned_droplets = service_config.get('assigned_droplets', [])
        
        if not assigned_droplets:
            return {
                'success': False,
                'error': f'No droplets assigned for {service_name}'
            }
        
        deployment_results = {}
        overall_success = True
        
        for droplet_name in assigned_droplets:
            try:
                droplet = self.state.get_droplet(droplet_name)
                if not droplet:
                    raise ValueError(f"Droplet {droplet_name} not found")
                
                result = self._deploy_service_to_droplet(
                    droplet['ip'], project, environment, service_name, service_def
                )
                
                deployment_results[droplet_name] = result
                
                if not result.get('success', False):
                    overall_success = False
                    
            except Exception as e:
                deployment_results[droplet_name] = {
                    'success': False,
                    'error': str(e)
                }
                overall_success = False
        
        return {
            'success': overall_success,
            'service': service_name,
            'droplets': deployment_results
        }
    
    def _deploy_service_to_droplet(self, droplet_ip: str, project: str, environment: str,
                                  service_name: str, service_def: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy infrastructure service to a specific droplet"""
        
        try:
            # Generate service configuration
            config = self._generate_service_config(project, environment, service_name, service_def)
            
            # Create Docker Compose configuration
            compose_content = self._generate_compose_config(service_name, config)
            
            # Deploy using Docker Compose
            result = self._deploy_compose_to_droplet(droplet_ip, service_name, compose_content)
            
            if result['success']:
                # Wait for service to be healthy
                health_result = self._wait_for_service_health(
                    droplet_ip, service_name, service_def.get('health_check')
                )
                result['health_check'] = health_result
            
            return result
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _generate_service_config(self, project: str, environment: str, 
                                service_name: str, service_def: Dict[str, Any]) -> Dict[str, Any]:
        """Generate configuration for infrastructure service"""
        
        # Generate resource hash for consistent naming
        resource_hash = self.state.generate_resource_hash(project, environment)
        
        # Get port allocation
        base_port = 5000 if service_name == 'postgresql' else \
                   6000 if service_name == 'redis' else \
                   9000 if service_name == 'opensearch' else 8000
        
        port = self.state.get_hash_based_port(project, environment, base_port, 1000)
        
        config = {
            'image': service_def['image'],
            'port': port,
            'container_name': f"{project}_{environment}_{service_name}",
            'environment': {},
            'volumes': service_def.get('volumes', []),
            'command': service_def.get('command')
        }
        
        # Process environment variables
        for key, value_template in service_def.get('environment', {}).items():
            if isinstance(value_template, str):
                # Replace placeholders
                value = value_template.format(
                    project=project,
                    environment=environment,
                    resource_hash=resource_hash[:8],
                    user=f'user_{resource_hash[:8]}',
                    db_password=self._get_secret('db_password', project, environment),
                    redis_password=self._get_secret('redis_password', project, environment),
                    opensearch_admin_password=self._get_secret('opensearch_admin_password', project, environment),
                    vault_root_token=self._get_secret('vault_root_token', project, environment)
                )
                config['environment'][key] = value
            else:
                config['environment'][key] = value_template
        
        # Process command template
        if config['command']:
            config['command'] = config['command'].format(
                redis_password=self._get_secret('redis_password', project, environment)
            )
        
        return config
    
    def _get_secret(self, secret_key: str, project: str, environment: str) -> str:
        """Get secret value or generate default"""
        
        secret_value = self.secret_manager.find_secret_value(secret_key, project, environment)
        
        if not secret_value:
            # Generate default values for development
            import secrets
            import string
            
            defaults = {
                'db_password': ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16)),
                'redis_password': ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16)),
                'opensearch_admin_password': ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16)),
                'vault_root_token': secrets.token_urlsafe(32)
            }
            
            secret_value = defaults.get(secret_key, 'changeme123')
            print(f"Warning: Using generated default for {secret_key}. Set environment variable {project.upper()}_{environment.upper()}_{secret_key.upper()}")
        
        return secret_value
    
    def _generate_compose_config(self, service_name: str, config: Dict[str, Any]) -> str:
        """Generate Docker Compose configuration for infrastructure service"""
        
        compose_config = f"""version: '3.8'
services:
  {service_name}:
    image: {config['image']}
    container_name: {config['container_name']}
    ports:
      - "{config['port']}:{config['port']}"
    environment:"""
        
        for key, value in config['environment'].items():
            compose_config += f"\n      - {key}={value}"
        
        if config.get('command'):
            compose_config += f"\n    command: {config['command']}"
        
        if config['volumes']:
            compose_config += "\n    volumes:"
            for volume in config['volumes']:
                volume_name = f"{config['container_name']}_data"
                compose_config += f"\n      - {volume_name}:{volume}"
        
        compose_config += "\n    restart: unless-stopped"
        compose_config += "\n    networks:\n      - infrastructure"
        
        # Add health check
        compose_config += f"""
    healthcheck:
      test: ["CMD-SHELL", "sleep 5"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s"""
        
        # Add networks and volumes
        compose_config += "\n\nnetworks:\n  infrastructure:\n    driver: bridge"
        
        if config['volumes']:
            compose_config += "\n\nvolumes:"
            for volume in config['volumes']:
                volume_name = f"{config['container_name']}_data"
                compose_config += f"\n  {volume_name}:\n    driver: local"
        
        return compose_config
    
    def _deploy_compose_to_droplet(self, droplet_ip: str, service_name: str, 
                                  compose_content: str) -> Dict[str, Any]:
        """Deploy Docker Compose configuration to droplet"""
        
        try:
            # Create temporary compose file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
                f.write(compose_content)
                temp_compose_path = f.name
            
            # Copy to droplet
            remote_path = f"/opt/app/{service_name}-infra-compose.yml"
            
            if not self.ssh_manager.copy_file_to_server(droplet_ip, temp_compose_path, remote_path):
                return {'success': False, 'error': 'Failed to copy compose file'}
            
            # Deploy
            success, stdout, stderr = self.ssh_manager.execute_remote_command(
                droplet_ip,
                f"cd /opt/app && docker-compose -f {service_name}-infra-compose.yml up -d",
                timeout=300
            )
            
            # Cleanup
            os.unlink(temp_compose_path)
            
            if success:
                return {
                    'success': True,
                    'output': stdout
                }
            else:
                return {
                    'success': False,
                    'error': stderr
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _wait_for_service_health(self, droplet_ip: str, service_name: str, 
                                health_check: Optional[str]) -> Dict[str, Any]:
        """Wait for service to become healthy"""
        
        if not health_check:
            return {'success': True, 'message': 'No health check defined'}
        
        import time
        
        max_attempts = 12  # 2 minutes with 10-second intervals
        
        for attempt in range(max_attempts):
            try:
                success, stdout, stderr = self.ssh_manager.execute_remote_command(
                    droplet_ip,
                    f"docker exec {service_name} {health_check}",
                    timeout=10
                )
                
                if success:
                    return {
                        'success': True,
                        'message': f'Service {service_name} is healthy',
                        'attempts': attempt + 1
                    }
                
                time.sleep(10)
                
            except Exception as e:
                time.sleep(10)
                continue
        
        return {
            'success': False,
            'message': f'Service {service_name} failed health check after {max_attempts} attempts'
        }
    
    def get_service_status(self, project: str, environment: str) -> Dict[str, Any]:
        """Get status of all infrastructure services"""
        
        project_key = f"{project}-{environment}"
        project_services = self.state.get_project_services(project_key)
        
        status = {}
        
        for service_name in ['postgresql', 'redis', 'opensearch', 'vault']:
            if service_name in project_services:
                service_config = project_services[service_name]
                assigned_droplets = service_config.get('assigned_droplets', [])
                
                service_status = {
                    'assigned_droplets': assigned_droplets,
                    'droplet_status': {}
                }
                
                for droplet_name in assigned_droplets:
                    droplet = self.state.get_droplet(droplet_name)
                    if droplet:
                        droplet_status = self._check_service_on_droplet(
                            droplet['ip'], f"{project}_{environment}_{service_name}"
                        )
                        service_status['droplet_status'][droplet_name] = droplet_status
                
                status[service_name] = service_status
        
        return status
    
    def _check_service_on_droplet(self, droplet_ip: str, container_name: str) -> Dict[str, Any]:
        """Check if service is running on droplet"""
        
        try:
            success, stdout, stderr = self.ssh_manager.execute_remote_command(
                droplet_ip,
                f"docker ps --filter name={container_name} --format '{{{{.Status}}}}'",
                timeout=10
            )
            
            if success and stdout.strip():
                status = stdout.strip()
                return {
                    'running': 'Up' in status,
                    'status': status
                }
            else:
                return {
                    'running': False,
                    'status': 'not_found'
                }
                
        except Exception as e:
            return {
                'running': False,
                'status': 'error',
                'error': str(e)
            }