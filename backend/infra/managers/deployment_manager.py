"""
Deployment Manager

Handles Git-based deployment with version tagging, platform-agnostic deployment,
and post-deployment snapshot creation.
"""

import os
import json
import subprocess
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from ..infrastructure_state import InfrastructureState
from ..environment_generator import EnvironmentGenerator
from .ssh_key_manager import SSHKeyManager


class GitManager:
    """
    Handles Git operations for deployment
    """
    
    def __init__(self, work_dir: str = "/tmp/deployments"):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        
    def clone_repository(self, repo_url: str, project: str, branch: str = "main") -> Path:
        """Clone repository to working directory"""
        
        project_dir = self.work_dir / project
        
        # Remove existing directory if it exists
        if project_dir.exists():
            shutil.rmtree(project_dir)
        
        try:
            result = subprocess.run([
                'git', 'clone', '-b', branch, repo_url, str(project_dir)
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                print(f"Repository {repo_url} cloned to {project_dir}")
                return project_dir
            else:
                raise RuntimeError(f"Git clone failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Git clone timed out for {repo_url}")
    
    def get_current_commit(self, project_dir: Path) -> str:
        """Get current commit hash"""
        try:
            result = subprocess.run([
                'git', 'rev-parse', 'HEAD'
            ], cwd=project_dir, capture_output=True, text=True)
            
            if result.returncode == 0:
                return result.stdout.strip()[:12]  # Short hash
            else:
                return "unknown"
        except Exception:
            return "unknown"
    
    def create_tag(self, project_dir: Path, tag_name: str, message: str = None) -> bool:
        """Create a Git tag"""
        try:
            cmd = ['git', 'tag', '-a', tag_name]
            if message:
                cmd.extend(['-m', message])
            else:
                cmd.extend(['-m', f'Automated tag: {tag_name}'])
            
            result = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True)
            return result.returncode == 0
        except Exception:
            return False
    
    def push_tag(self, project_dir: Path, tag_name: str) -> bool:
        """Push tag to remote repository"""
        try:
            result = subprocess.run([
                'git', 'push', 'origin', tag_name
            ], cwd=project_dir, capture_output=True, text=True, timeout=30)
            
            return result.returncode == 0
        except Exception:
            return False
    
    def get_latest_tag(self, project_dir: Path, pattern: str = None) -> Optional[str]:
        """Get latest tag matching pattern"""
        try:
            cmd = ['git', 'tag', '--sort=-version:refname']
            if pattern:
                cmd.extend(['--list', pattern])
            
            result = subprocess.run(cmd, cwd=project_dir, capture_output=True, text=True)
            
            if result.returncode == 0:
                tags = result.stdout.strip().split('\n')
                return tags[0] if tags and tags[0] else None
        except Exception:
            pass
        
        return None


class VersionManager:
    """
    Manages version tagging and promotion strategy
    """
    
    def __init__(self, git_manager: GitManager):
        self.git_manager = git_manager
        
    def get_next_version(self, project_dir: Path) -> str:
        """Get next semantic version"""
        latest_tag = self.git_manager.get_latest_tag(project_dir, "v*")
        
        if not latest_tag:
            return "1.0.0"
        
        # Parse version (v1.2.3 -> 1.2.3)
        try:
            version_part = latest_tag.lstrip('v').split('-')[0]  # Remove v prefix and any suffixes
            major, minor, patch = map(int, version_part.split('.'))
            
            # Increment patch version
            return f"{major}.{minor}.{patch + 1}"
        except (ValueError, IndexError):
            return "1.0.0"
    
    def create_uat_version_tag(self, project_dir: Path, project: str) -> str:
        """Create version tag after successful UAT deployment"""
        timestamp = datetime.now().strftime('%Y%m%d-%H%M')
        version = self.get_next_version(project_dir)
        tag_name = f"v{version}-uat-{timestamp}"
        
        message = f"UAT deployment tag for {project} v{version}"
        
        if self.git_manager.create_tag(project_dir, tag_name, message):
            if self.git_manager.push_tag(project_dir, tag_name):
                print(f"Created and pushed UAT tag: {tag_name}")
                return tag_name
            else:
                print(f"Created tag {tag_name} but failed to push")
                return tag_name
        else:
            raise RuntimeError(f"Failed to create UAT tag {tag_name}")
    
    def get_latest_uat_tag(self, project_dir: Path) -> Optional[str]:
        """Get latest UAT tag for production deployment"""
        return self.git_manager.get_latest_tag(project_dir, "v*-uat-*")


class DeploymentManager:
    """
    Platform-agnostic deployment manager with Git integration
    """
    
    def __init__(self, infrastructure_state: InfrastructureState, 
                 environment_generator: EnvironmentGenerator,
                 ssh_key_manager: SSHKeyManager,
                 deployment_config: Dict[str, Any]):
        self.state = infrastructure_state
        self.env_generator = environment_generator
        self.ssh_manager = ssh_key_manager
        self.config = deployment_config
        
        self.git_manager = GitManager()
        self.version_manager = VersionManager(self.git_manager)
        
        # Get platform deployer
        platform = deployment_config.get('deployment_platform', 'docker')
        self.deployer = self._get_platform_deployer(platform)
    
    def _get_platform_deployer(self, platform: str):
        """Factory pattern for different deployment platforms"""
        if platform == "docker":
            return DockerDeployer(self.ssh_manager)
        elif platform == "kubernetes":
            return KubernetesDeployer(self.ssh_manager)
        elif platform == "podman":
            return PodmanDeployer(self.ssh_manager)
        else:
            raise ValueError(f"Unsupported platform: {platform}")
    
    def deploy_to_uat(self, project: str, branch: str = "main") -> Dict[str, Any]:
        """Deploy to UAT and create version tag"""
        
        print(f"Starting UAT deployment for {project} from branch {branch}")
        
        # Get project configuration
        project_config = self.config['projects'].get(project)
        if not project_config:
            raise ValueError(f"Project {project} not found in deployment config")
        
        # Clone repository
        repo_url = project_config['git_repo']
        project_dir = self.git_manager.clone_repository(repo_url, project, branch)
        
        # Deploy to UAT environment
        deployment_result = self.deploy_environment(
            project=project,
            environment="uat",
            project_dir=project_dir
        )
        
        if deployment_result['success']:
            # Create version tag after successful UAT deployment
            try:
                version_tag = self.version_manager.create_uat_version_tag(project_dir, project)
                
                return {
                    "status": "success",
                    "environment": "uat",
                    "version_tag": version_tag,
                    "git_commit": self.git_manager.get_current_commit(project_dir),
                    "ready_for_prod": True,
                    "deployment_result": deployment_result
                }
            except Exception as e:
                print(f"Warning: UAT deployment succeeded but tagging failed: {e}")
                return {
                    "status": "success_no_tag",
                    "environment": "uat",
                    "version_tag": None,
                    "git_commit": self.git_manager.get_current_commit(project_dir),
                    "ready_for_prod": False,
                    "deployment_result": deployment_result,
                    "tag_error": str(e)
                }
        else:
            return {
                "status": "failed",
                "environment": "uat",
                "deployment_result": deployment_result
            }
    
    def deploy_to_prod(self, project: str, use_uat_tag: bool = True, 
                      specific_tag: str = None) -> Dict[str, Any]:
        """Deploy to prod using UAT-tested version tag"""
        
        print(f"Starting production deployment for {project}")
        
        # Get project configuration
        project_config = self.config['projects'].get(project)
        if not project_config:
            raise ValueError(f"Project {project} not found in deployment config")
        
        # Determine which version to deploy
        if specific_tag:
            git_ref = specific_tag
        elif use_uat_tag:
            # Clone to get latest UAT tag
            repo_url = project_config['git_repo']
            temp_dir = self.git_manager.clone_repository(repo_url, f"{project}_temp", "main")
            
            latest_uat_tag = self.version_manager.get_latest_uat_tag(temp_dir)
            if not latest_uat_tag:
                return {
                    "status": "failed",
                    "error": "No UAT tag found for production deployment"
                }
            git_ref = latest_uat_tag
        else:
            git_ref = "main"
        
        print(f"Deploying {project} to production using {git_ref}")
        
        # Clone repository at specific tag/branch
        repo_url = project_config['git_repo']
        project_dir = self.git_manager.clone_repository(repo_url, f"{project}_prod", git_ref)
        
        # Deploy to production environment
        deployment_result = self.deploy_environment(
            project=project,
            environment="prod",
            project_dir=project_dir,
            reuse_uat_images=use_uat_tag
        )
        
        return {
            "status": "success" if deployment_result['success'] else "failed",
            "environment": "prod",
            "git_ref": git_ref,
            "git_commit": self.git_manager.get_current_commit(project_dir),
            "deployment_result": deployment_result
        }
    
    def deploy_environment(self, project: str, environment: str, 
                          project_dir: Path, reuse_uat_images: bool = False) -> Dict[str, Any]:
        """Deploy all services for a project environment"""
        
        project_config = self.config['projects'].get(project, {})
        services_config = project_config.get('services', {})
        
        deployment_results = {}
        overall_success = True
        
        for service_type, service_config in services_config.items():
            print(f"Deploying {project}-{environment}-{service_type}")
            
            try:
                result = self.deploy_service(
                    project=project,
                    environment=environment,
                    service_type=service_type,
                    service_config=service_config,
                    project_dir=project_dir,
                    reuse_uat_image=reuse_uat_images
                )
                
                deployment_results[service_type] = result
                
                if not result.get('success', False):
                    overall_success = False
                    
            except Exception as e:
                deployment_results[service_type] = {
                    'success': False,
                    'error': str(e)
                }
                overall_success = False
                print(f"Failed to deploy {service_type}: {e}")
        
        return {
            'success': overall_success,
            'services': deployment_results,
            'project': project,
            'environment': environment
        }
    
    def deploy_service(self, project: str, environment: str, service_type: str,
                      service_config: Dict[str, Any], project_dir: Path,
                      reuse_uat_image: bool = False) -> Dict[str, Any]:
        """Deploy a single service"""
        
        # Build or get image
        if reuse_uat_image:
            image_name = f"{project}-{service_type}:uat-latest"
            print(f"Reusing UAT image: {image_name}")
        else:
            image_name = self._build_service_image(project, service_type, service_config, project_dir)
        
        # Generate environment variables and template context
        template_context = self.env_generator.generate_template_context(
            project, environment, service_type, service_config, image_name
        )
        
        # Get target droplets from infrastructure state
        project_key = f"{project}-{environment}"
        state_service_config = self.state.get_project_services(project_key).get(service_type, {})
        target_droplets = state_service_config.get('assigned_droplets', [])
        
        if not target_droplets:
            return {
                'success': False,
                'error': f'No droplets assigned for {project}-{environment}-{service_type}'
            }
        
        # Deploy using platform-specific deployer
        deployment_result = self.deployer.deploy(
            template_context=template_context,
            target_droplets=target_droplets,
            service_config=service_config
        )
        
        # Create post-deployment snapshot if successful
        if deployment_result.get('success', False):
            self._create_post_deployment_snapshots(target_droplets, project, environment, service_type)
        
        return deployment_result
    
    def _build_service_image(self, project: str, service_type: str, 
                           service_config: Dict[str, Any], project_dir: Path) -> str:
        """Build Docker image for service"""
        
        dockerfile_path = service_config.get('dockerfile_path', 'Dockerfile')
        build_context = service_config.get('build_context', '.')
        
        # Generate image name with timestamp
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        image_name = f"{project}-{service_type}:{timestamp}"
        
        build_path = project_dir / build_context
        full_dockerfile_path = project_dir / dockerfile_path
        
        if not full_dockerfile_path.exists():
            raise FileNotFoundError(f"Dockerfile not found: {full_dockerfile_path}")
        
        try:
            print(f"Building image {image_name} from {build_path}")
            
            result = subprocess.run([
                'docker', 'build',
                '-t', image_name,
                '-f', str(full_dockerfile_path),
                str(build_path)
            ], capture_output=True, text=True, timeout=600)  # 10 minute timeout
            
            if result.returncode == 0:
                print(f"Successfully built image: {image_name}")
                return image_name
            else:
                raise RuntimeError(f"Docker build failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Docker build timed out for {image_name}")
    
    def _create_post_deployment_snapshots(self, droplets: List[str], project: str, 
                                        environment: str, service_type: str):
        """Create snapshots after successful deployment"""
        
        timestamp = datetime.now().strftime('%Y%m%d-%H%M')
        
        for droplet_name in droplets:
            snapshot_name = f"{droplet_name}-deploy-{project}-{environment}-{timestamp}"
            
            try:
                # This would integrate with DigitalOcean API
                print(f"Creating post-deployment snapshot: {snapshot_name}")
                # TODO: Integrate with DigitalOceanManager for actual snapshot creation
                
            except Exception as e:
                print(f"Warning: Failed to create snapshot {snapshot_name}: {e}")


class DockerDeployer:
    """
    Docker-based deployment using Docker Compose
    """
    
    def __init__(self, ssh_manager: SSHKeyManager):
        self.ssh_manager = ssh_manager
    
    def deploy(self, template_context: Dict[str, Any], target_droplets: List[str], 
              service_config: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy service using Docker Compose"""
        
        # Generate docker-compose.yml content
        compose_content = self._generate_compose_config(template_context)
        
        deployment_results = {}
        overall_success = True
        
        for droplet_name in target_droplets:
            try:
                droplet_ip = self._get_droplet_ip(droplet_name)
                result = self._deploy_to_droplet(droplet_ip, compose_content, template_context)
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
            'platform': 'docker',
            'droplets': deployment_results
        }
    
    def _generate_compose_config(self, context: Dict[str, Any]) -> str:
        """Generate docker-compose.yml content"""
        
        # Basic docker-compose template
        compose_template = f"""version: '3.8'
services:
  {context['service_name']}:
    image: {context['image_name']}"""
        
        # Add command for workers
        if context.get('command'):
            compose_template += f"\n    command: {context['command']}"
        
        # Add environment variables
        compose_template += "\n    environment:"
        env_vars = ['DB_USER', 'DB_NAME', 'DB_HOST', 'DB_PORT', 'REDIS_HOST', 'REDIS_PORT',
                   'VAULT_HOST', 'VAULT_PORT', 'OPENSEARCH_HOST', 'OPENSEARCH_PORT',
                   'SERVICE_NAME', 'ENVIRONMENT', 'PROJECT', 'RESOURCE_HASH']
        
        for var in env_vars:
            if var in context:
                compose_template += f"\n      - {var}={context[var]}"
        
        # Add SERVICE_PORT for web services only
        if not context.get('is_worker') and 'SERVICE_PORT' in context:
            compose_template += f"\n      - SERVICE_PORT={context['SERVICE_PORT']}"
        
        # Add secrets
        if context.get('secrets'):
            compose_template += "\n    secrets:"
            for secret in context['secrets']:
                compose_template += f"\n      - {secret}"
        
        # Add ports for web services
        if not context.get('is_worker') and 'SERVICE_PORT' in context:
            port = context['SERVICE_PORT']
            compose_template += f"\n    ports:\n      - \"{port}:{port}\""
        
        compose_template += "\n    restart: unless-stopped"
        compose_template += "\n    networks:\n      - app-network"
        
        # Add networks section
        compose_template += "\n\nnetworks:\n  app-network:\n    driver: bridge"
        
        # Add secrets section
        if context.get('secrets'):
            compose_template += "\n\nsecrets:"
            for secret in context['secrets']:
                compose_template += f"\n  {secret}:\n    external: true"
        
        return compose_template
    
    def _deploy_to_droplet(self, droplet_ip: str, compose_content: str, 
                          context: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy to a specific droplet"""
        
        service_name = context['service_name']
        
        try:
            # Create temporary compose file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
                f.write(compose_content)
                temp_compose_path = f.name
            
            # Copy compose file to droplet
            remote_path = f"/opt/app/{service_name}-compose.yml"
            
            if not self.ssh_manager.copy_file_to_server(droplet_ip, temp_compose_path, remote_path):
                return {'success': False, 'error': 'Failed to copy compose file'}
            
            # Deploy using docker-compose
            success, stdout, stderr = self.ssh_manager.execute_remote_command(
                droplet_ip,
                f"cd /opt/app && docker-compose -f {service_name}-compose.yml up -d",
                timeout=300
            )
            
            # Cleanup temp file
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
    
    def _get_droplet_ip(self, droplet_name: str) -> str:
        """Get droplet IP from infrastructure state"""
        # This would be injected or accessed through infrastructure state
        # For now, placeholder implementation
        return "placeholder_ip"


class KubernetesDeployer:
    """
    Kubernetes-based deployment
    """
    
    def __init__(self, ssh_manager: SSHKeyManager):
        self.ssh_manager = ssh_manager
    
    def deploy(self, template_context: Dict[str, Any], target_droplets: List[str], 
              service_config: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy service using Kubernetes"""
        # TODO: Implement Kubernetes deployment
        return {
            'success': False,
            'error': 'Kubernetes deployment not yet implemented'
        }


class PodmanDeployer:
    """
    Podman-based deployment
    """
    
    def __init__(self, ssh_manager: SSHKeyManager):
        self.ssh_manager = ssh_manager
    
    def deploy(self, template_context: Dict[str, Any], target_droplets: List[str], 
              service_config: Dict[str, Any]) -> Dict[str, Any]:
        """Deploy service using Podman"""
        # TODO: Implement Podman deployment
        return {
            'success': False,
            'error': 'Podman deployment not yet implemented'
        }
