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
from ..managers.ssh_key_manager import SSHKeyManager
from ..platform import PlatformManager


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
        environment_generator.container_secret_manager.set_platform(platform)
        self.deployer = self._get_platform_deployer(platform)
        self.build_command = self._get_build_command(platform)
        self.platform_manager = PlatformManager(
            platform=platform,
            secret_manager=environment_generator.container_secret_manager.secret_manager
        )

    def _get_build_command(self, platform: str) -> str:
        """Get the container build command for the platform"""
        platform_commands = {
            'docker': 'docker',
            'podman': 'podman', 
            'kubernetes': 'docker',  # K8s typically uses docker daemon or buildah
        }
        
        command = platform_commands.get(platform, 'docker')
        
        # Verify command exists
        try:
            subprocess.run([command, '--version'], capture_output=True, check=True)
            return command
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback to docker if platform command not available
            return 'docker'
           
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
    
    def _get_project_git_url(self, project: str) -> str:
        """Generate Git URL from project name using configured pattern"""
        git_config = self.config.get('git_config', {})
        base_url = git_config.get('base_url', 'https://github.com/yourorg')
        url_pattern = git_config.get('url_pattern', '{base_url}/{project}.git')
        
        return url_pattern.format(base_url=base_url, project=project)
    
    def _get_shared_libs_repo(self) -> str:
        """Get shared-libs repository URL"""
        return self._get_project_git_url("shared-libs")
    
    def _should_auto_commit(self) -> bool:
        """Check if we should auto-commit before deployment"""
        return self.config.get('auto_commit_before_deploy', True)
    
    def _get_current_commit_hash(self, repo_dir: Path) -> str:
        """Get current commit hash of a repository"""
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], 
                              cwd=repo_dir, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    
    def _commit_and_tag_local_changes(self, project_dir: Path, project: str):
        """Commit changes in both project and shared-libs, then create unified deployment tags"""
        
        try:
            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            unified_tag = f"deploy-uat-{timestamp}"
            
            # 1. Handle shared-libs first
            shared_libs_link = project_dir / "shared-libs"
            shared_libs_committed = False
            shared_libs_hash = None
            
            if shared_libs_link.is_symlink():
                shared_libs_dir = shared_libs_link.resolve()
                print(f"Found shared-libs at: {shared_libs_dir}")
                
                # Check for uncommitted changes in shared-libs
                result = subprocess.run(['git', 'status', '--porcelain'], 
                                      cwd=shared_libs_dir, capture_output=True, text=True)
                
                if result.stdout.strip():
                    print("Committing shared-libs changes...")
                    subprocess.run(['git', 'add', '-A'], cwd=shared_libs_dir, check=True)
                    subprocess.run(['git', 'commit', '-m', f'Pre-deployment commit {timestamp}'], 
                                 cwd=shared_libs_dir, check=True)
                    subprocess.run(['git', 'push', 'origin', 'main'], cwd=shared_libs_dir, check=True)
                    shared_libs_committed = True
                    print(f"✓ Committed and pushed shared-libs changes")
                
                # Get current commit hash (whether we committed or not)
                shared_libs_hash = self._get_current_commit_hash(shared_libs_dir)
                
                # Create unified tag on shared-libs
                tag_message = f'UAT deployment {timestamp} for {project}'
                subprocess.run(['git', 'tag', '-a', unified_tag, '-m', tag_message], 
                             cwd=shared_libs_dir, check=True)
                subprocess.run(['git', 'push', 'origin', unified_tag], cwd=shared_libs_dir, check=True)
                print(f"✓ Tagged shared-libs with: {unified_tag}")
            
            # 2. Handle project code
            result = subprocess.run(['git', 'status', '--porcelain'], 
                                  cwd=project_dir, capture_output=True, text=True)
            
            project_committed = False
            if result.stdout.strip():
                print(f"Committing {project} changes...")
                subprocess.run(['git', 'add', '-A'], cwd=project_dir, check=True)
                
                commit_msg = f'Pre-deployment commit {timestamp}'
                if shared_libs_committed:
                    commit_msg += f' (shared-libs: {shared_libs_hash[:8]})'
                
                subprocess.run(['git', 'commit', '-m', commit_msg], 
                             cwd=project_dir, check=True)
                subprocess.run(['git', 'push', 'origin', 'main'], cwd=project_dir, check=True)
                project_committed = True
                print(f"✓ Committed and pushed {project} changes")
            
            # 3. Create unified tag on project
            project_hash = self._get_current_commit_hash(project_dir)
            tag_message = f'UAT deployment {timestamp}'
            if shared_libs_hash:
                tag_message += f' (shared-libs: {shared_libs_hash[:8]})'
            
            subprocess.run(['git', 'tag', '-a', unified_tag, '-m', tag_message], 
                         cwd=project_dir, check=True)
            subprocess.run(['git', 'push', 'origin', unified_tag], cwd=project_dir, check=True)
            
            print(f"✓ Tagged {project} with: {unified_tag}")
            print(f"📦 Deployment tagged: {unified_tag}")
            print(f"   - Project commit: {project_hash[:8]}")
            if shared_libs_hash:
                print(f"   - Shared-libs commit: {shared_libs_hash[:8]}")
            
            return {
                'unified_tag': unified_tag,
                'project_hash': project_hash,
                'shared_libs_hash': shared_libs_hash,
                'shared_libs_committed': shared_libs_committed,
                'project_committed': project_committed,
                'timestamp': timestamp
            }
            
        except subprocess.CalledProcessError as e:
            print(f"Error during commit/tag: {e}")
            raise RuntimeError(f"Failed to commit and tag: {e}")
    
    def reproduce_deployment(self, tag_name: str, target_dir: str = None) -> Dict[str, Any]:
        """Reproduce exact deployment state from unified tag"""
        
        if not target_dir:
            target_dir = f"./reproduced-{tag_name}"
        
        target_path = Path(target_dir)
        target_path.mkdir(parents=True, exist_ok=True)
        
        print(f"🔍 Reproducing deployment: {tag_name}")
        
        try:
            # 1. Clone shared-libs at specific tag
            shared_libs_repo = self._get_shared_libs_repo()
            shared_libs_dir = target_path / "shared-libs"
            
            subprocess.run([
                'git', 'clone', '-b', tag_name, '--depth', '1',
                shared_libs_repo, str(shared_libs_dir)
            ], check=True)
            print(f"✓ Cloned shared-libs at tag {tag_name}")
            
            # 2. Clone all projects that might use this tag
            reproduced_projects = []
            for project_name in self.config['projects'].keys():
                try:
                    project_repo = self._get_project_git_url(project_name)
                    project_dir = target_path / project_name
                    
                    subprocess.run([
                        'git', 'clone', '-b', tag_name, '--depth', '1',
                        project_repo, str(project_dir)
                    ], check=True, capture_output=True)
                    
                    # Create symlink to shared-libs for local development
                    project_shared_libs = project_dir / "shared-libs"
                    if project_shared_libs.is_symlink():
                        project_shared_libs.unlink()
                    elif project_shared_libs.exists():
                        shutil.rmtree(project_shared_libs)
                    
                    # Create relative symlink
                    project_shared_libs.symlink_to("../shared-libs")
                    
                    reproduced_projects.append(project_name)
                    print(f"✓ Cloned {project_name} at tag {tag_name}")
                    
                except subprocess.CalledProcessError:
                    # Project doesn't have this tag - skip
                    continue
            
            print(f"🎯 Reproduction complete in: {target_path}")
            print(f"📋 Reproduced projects: {', '.join(reproduced_projects)}")
            
            return {
                'success': True,
                'target_dir': str(target_path),
                'tag_name': tag_name,
                'reproduced_projects': reproduced_projects,
                'shared_libs_available': True
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'target_dir': str(target_path)
            }
    
    def deploy_to_uat(self, project: str, branch: str = "main", use_local: bool = False, 
                      local_project_path: str = None) -> Dict[str, Any]:
        """Deploy project to UAT environment"""
        
        print(f"🚀 Deploying {project} to UAT")
        
        if use_local:
            # Use local codebase (already tested)
            if not local_project_path:
                local_project_path = f"../{project}"  # Assume projects are siblings
            
            project_dir = Path(local_project_path).resolve()
            if not project_dir.exists():
                raise ValueError(f"Local project path not found: {project_dir}")
            
            print(f"Using local codebase: {project_dir}")
            
            # Commit both shared-libs and project changes
            commit_info = None
            if self._should_auto_commit():
                commit_info = self._commit_and_tag_local_changes(project_dir, project)
        else:
            # Traditional git clone approach
            project_config = self.config['projects'].get(project)
            if not project_config:
                raise ValueError(f"Project {project} not found in deployment config")
            
            repo_url = self._get_project_git_url(project)
            project_dir = self.git_manager.clone_repository(repo_url, project, branch)
            
            # Still need to resolve shared-libs for git-cloned projects
            self._resolve_shared_libs(project_dir)
        
        # Deploy using the project directory (local or cloned)
        deployment_result = self.deploy_environment(
            project=project,
            environment="uat",
            project_dir=project_dir
        )
        
        result = {
            "status": "success" if deployment_result['success'] else "failed",
            "environment": "uat",
            "source": "local" if use_local else "git",
            "project_path": str(project_dir),
            "deployment_result": deployment_result
        }
        
        # Add commit info if we auto-committed
        if commit_info:
            result['commit_info'] = commit_info
        
        return result
    
    def _resolve_shared_libs(self, project_dir: Path) -> bool:
        """Replace symlinks with actual shared-libs for deployment"""
        
        shared_libs_link = project_dir / "shared-libs"
        
        # Check if project uses shared-libs (symlink or reference)
        if shared_libs_link.is_symlink() or self._dockerfile_references_shared_libs(project_dir):
            print(f"Resolving shared-libs for {project_dir.name}")
            
            # Remove symlink if it exists
            if shared_libs_link.is_symlink():
                shared_libs_link.unlink()
            
            # Clone fresh copy of shared-libs
            shared_libs_repo = self._get_shared_libs_repo()
            temp_shared_libs = self.git_manager.clone_repository(
                shared_libs_repo, f"shared-libs-{project_dir.name}", "main"
            )
            
            # Copy into project
            shutil.copytree(temp_shared_libs, shared_libs_link)
            print(f"✓ Copied shared-libs to {shared_libs_link}")
            
        return True
    
    def _dockerfile_references_shared_libs(self, project_dir: Path) -> bool:
        """Check if any Dockerfile references shared-libs"""
        for dockerfile in project_dir.glob("**/Dockerfile"):
            if "shared-libs" in dockerfile.read_text():
                return True
        return False
    
    def deploy_to_prod(self, project: str, use_uat_tag: bool = True, 
                      specific_tag: str = None) -> Dict[str, Any]:
        """Deploy project to production environment"""
        
        if not self.deployment_manager:
            return {'success': False, 'error': 'Deployment manager not initialized'}
        
        print(f"🚀 Deploying {project} to production")
        
        try:
            result = self.deployment_manager.deploy_to_prod(project, use_uat_tag)
            
            if result['status'] == 'success':
                # Update load balancer after deployment
                lb_result = self.load_balancer_manager.deploy_nginx_config()
                result['load_balancer_update'] = lb_result
            
            return result
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
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
        """Build container image for service"""
        
        containerfile_path = service_config.get('containerfile_path', 'Dockerfile')
        build_context = service_config.get('build_context', '.')
        
        # Generate image name with timestamp
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        image_name = f"{project}-{service_type}:{timestamp}"
        
        build_path = project_dir / build_context
        full_containerfile_path = project_dir / containerfile_path
        
        if not full_containerfile_path.exists():
            raise FileNotFoundError(f"containerfile not found: {full_containerfile_path}")
        
        try:
            print(f"Building image {image_name} using {self.build_command}")
            
            result = subprocess.run([
                self.build_command, 'build', 
                '-t', image_name,
                '-f', str(full_containerfile_path),
                str(build_path)
            ], capture_output=True, text=True, timeout=600)  # 10 minute timeout
            
            if result.returncode == 0:
                print(f"Successfully built image: {image_name}")
                return image_name
            else:
                raise RuntimeError(f"{self.build_command} build failed: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{self.build_command} build timed out for {image_name}")
    
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
        droplet = self.infrastructure_state.get_droplet(droplet_name)
        if droplet:
            return droplet['ip']
        raise ValueError(f"Droplet {droplet_name} not found in infrastructure state")


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