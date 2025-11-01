import os
import time
import json
import subprocess
import traceback
from datetime import datetime
import requests
from typing import Dict, Any, List, Optional
from uuid import uuid4
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

try:
    from .temp_storage import TempStorage
except ImportError:
    from temp_storage import TempStorage
try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .nginx_config_generator import NginxConfigGenerator
except ImportError:
    from nginx_config_generator import NginxConfigGenerator
try:
    from .deployment_config import DeploymentConfigurer
except ImportError:
    from deployment_config import DeploymentConfigurer
try:
    from .live_deployment_query import LiveDeploymentQuery
except ImportError:
    from live_deployment_query import LiveDeploymentQuery
try:
    from .deployment_syncer import DeploymentSyncer
except ImportError:
    from deployment_syncer import DeploymentSyncer
try:
    from .execute_docker import DockerExecuter
except ImportError:
    from execute_docker import DockerExecuter
try:
    from .deployment_state_manager import DeploymentStateManager
except ImportError:
    from deployment_state_manager import DeploymentStateManager
try:
    from .cron_manager import CronManager
except ImportError:
    from cron_manager import CronManager
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .do_cost_tracker import DOCostTracker
except ImportError:
    from do_cost_tracker import DOCostTracker
try:
    from . import env_loader
except ImportError:
    import env_loader
try:
    from .do_manager import DOManager
except ImportError:
    from do_manager import DOManager
try:
    from .backup_manager import BackupManager
except ImportError:
    from backup_manager import BackupManager
try:
    from .encryption import Encryption
except ImportError:
    from encryption import Encryption
try:
    from .resource_resolver import ResourceResolver
except ImportError:
    from resource_resolver import ResourceResolver
try:
    from .git_manager import GitManager
except ImportError:
    from git_manager import GitManager
try:
    from .server_inventory import ServerInventory
except ImportError:
    from server_inventory import ServerInventory
try:
    from .path_resolver import PathResolver
except ImportError:
    from path_resolver import PathResolver


def log(msg):
    Logger.log(msg)


class Deployer:
    """
    Handles building Docker images and deploying services with integrated sync operations.
    Works with project-based configuration structure.

    Attributes:
        id (str): Unique identifier for this deployment instance.
        project_name (str): Name of the project being deployed.
        deployment_configurer (DeploymentConfigurer): Provides configuration access and helper methods.
    """

    def __init__(self, user: str, project_name: str):
        """
        Initialize a Deployer instance for a specific project.

        Args:
            user: user id (e.g. "u1")
            project_name: Name of the project to deploy            
            
        Raises:
            ValueError: If project_name not specified
            FileNotFoundError: If project not found
        """
        if not project_name:
            projects = DeploymentConfigurer.list_projects(user)
            if projects:
                raise ValueError(
                    f"Must specify project_name. Available projects: {', '.join(projects)}"
                )
            else:
                raise ValueError("No projects found in config/projects/")
        
        self.id = f'deployment_{uuid4()}'
        self.user = user
        self.project_name = project_name
        self.deployment_configurer = DeploymentConfigurer(user, project_name)
        
        # Save debug configs
        debug_path = TempStorage.get_deployment_debug_path(self.id)
        
        with open(debug_path / 'raw_config.json', 'w') as f:
            json.dump(self.deployment_configurer.raw_config, f, indent=4)
        
        with open(debug_path / 'project_info.txt', 'w') as f:
            f.write(f"Project: {self.project_name}\n")
            f.write(f"Deployment ID: {self.id}\n")
        
        self.deployment_configurer.save_final_config(self.id)
        
        log(f"Initialized Deployer for project: {self.project_name}")

    def _get_version(self) -> str:
        """Get version - either override or from config"""
        if hasattr(self, '_override_version') and self._override_version:
            return self._override_version
        return self.deployment_configurer.get_version()

    # =============================================================================
    # PUBLIC SYNC API - Manual sync operations
    # =============================================================================

    def push_config(self, env: str = None, targets: List[str] = None) -> bool:
        """
        Manually push config, secrets, and files to servers.
        
        Args:
            env: Environment name, defaults to all environments
            targets: Target servers, defaults to project servers
            
        Returns:
            True if push completed successfully
        """     
        if env:
            return DeploymentSyncer.push(self.user, self.project_name, env, targets)
        else:
            # Push to all environments
            success = True
            for environment in self.deployment_configurer.get_environments():
                if not DeploymentSyncer.push(self.user, self.project_name, environment, targets):
                    success = False
            return success

    def pull_data(self, env: str = None, targets: List[str] = None) -> bool:
        """
        Manually pull data, logs, backups, and monitoring data from containers/servers.
        
        Args:
            env: Environment name, defaults to all environments  
            targets: Target servers, defaults to project servers
            
        Returns:
            True if pull completed successfully
        """        
        if env:
            return DeploymentSyncer.pull(self.user, self.project_name, env, targets)
        else:
            # Pull from all environments
            success = True
            for environment in self.deployment_configurer.get_environments():
                if not DeploymentSyncer.pull(self.user, self.project_name, environment, targets):
                    success = False
            return success

    def full_sync(self, env: str = None, targets: List[str] = None) -> bool:
        """
        Manually perform full bidirectional sync - push config and pull data.
        
        Args:
            env: Environment name, defaults to all environments
            targets: Target servers, defaults to project servers
            
        Returns:
            True if sync completed successfully
        """        
        if env:
            return DeploymentSyncer.sync(self.user, self.project_name, env, targets)
        else:
            # Sync all environments
            success = True
            for environment in self.deployment_configurer.get_environments():
                if not DeploymentSyncer.sync(self.user, self.project_name, environment, targets):
                    success = False
            return success

    # =============================================================================
    # DEPLOYMENT METHODS WITH SYNC INTEGRATION
    # =============================================================================

    def is_service_scheduled(self, service_config: Dict[str, Any]) -> bool:
        """Check if a service is scheduled (has cron schedule)"""
        schedule = service_config.get("schedule")
        return schedule is not None and CronManager.validate_cron_schedule(schedule)

    def _has_remote_servers(self, env: str = None) -> bool:
        """
        Check if any services target remote servers (non-localhost).
        
        Args:
            env: Environment to check, or None for all environments
            
        Returns:
            True if any remote servers are configured
        """
        environments = [env] if env else self.deployment_configurer.get_environments()
        
        for environment in environments:
            for service_name, service_config in self.deployment_configurer.get_services(environment).items():
                # Check if service has server specifications
                zone = service_config.get("server_zone")
                servers_count = service_config.get("servers_count", 0)
                
                # If zone specified and not localhost, it's remote
                if zone and zone != "localhost" and servers_count > 0:
                    return True
                
                # Legacy: check old 'servers' list format
                servers = service_config.get('servers', [])
                for server in servers:
                    if server and server != 'localhost':
                        return True
        
        return False

    def inject_app_directories_to_dockerfile(self, dockerfile_path: str, service_name: str) -> str:
        """
        Inject /app directory creation into Dockerfile if not already present.
        Updates existing injection if volume configuration has changed.
        """
        if not os.path.exists(dockerfile_path):
            return dockerfile_path
        
        with open(dockerfile_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Check if we already have auto-generated directories
        auto_gen_start = "# AUTO-GENERATED: Create required directories for volume mounts"
        auto_gen_end = "# END AUTO-GENERATED"
        
        lines = content.split('\n')
        
        # Find existing auto-generated section
        start_idx = -1
        end_idx = -1
        
        for i, line in enumerate(lines):
            if auto_gen_start in line:
                start_idx = i
            elif auto_gen_end in line and start_idx != -1:
                end_idx = i
                break
        
        # Use localhost for Dockerfile generation (build context)
        volumes = PathResolver.generate_all_volume_mounts(
            self.user, "temp", "temp", service_name,
            server_ip="localhost",  # Dockerfile build happens locally
            use_docker_volumes=True,           
            auto_create_dirs=False  # Don't create dirs during Dockerfile gen
        )
        app_dirs = set()
        
        for volume in volumes:
            if ':' in volume:
                container_path = volume.split(':', 1)[1].split(':')[0]
                if container_path.startswith('/app/'):
                    app_dirs.add(container_path)
        
        # If no /app directories found, remove existing injection if any
        if not app_dirs:
            if start_idx != -1 and end_idx != -1:
                lines[start_idx:end_idx+1] = []
                modified_content = '\n'.join(lines)
                with open(dockerfile_path, 'w', encoding='utf-8') as f:
                    f.write(modified_content)
                log(f"Removed /app directory injection from {dockerfile_path} (no /app volumes needed)")
            return dockerfile_path
        
        # Sort directories for consistent output
        sorted_dirs = sorted(app_dirs)
        
        # Build the new directory creation section - SIMPLIFIED
        new_section = [
            auto_gen_start,
            f"RUN mkdir -p {' '.join(sorted_dirs)}",
            auto_gen_end
        ]
        
        if start_idx != -1 and end_idx != -1:
            # Replace existing section
            lines[start_idx:end_idx+1] = new_section
            log(f"Updated /app directories in {dockerfile_path}: {sorted_dirs}")
        else:
            # Find insertion point for new section
            insert_index = -1
            
            for i, line in enumerate(lines):
                line_upper = line.strip().upper()
                if line_upper.startswith('WORKDIR /APP'):
                    insert_index = i + 1
                    break
                elif line_upper.startswith(('CMD', 'ENTRYPOINT')):
                    insert_index = i
                    break
            
            if insert_index == -1:
                insert_index = len(lines) - 1
            
            # Insert new section
            for i, new_line in enumerate([""] + new_section + [""]):
                lines.insert(insert_index + i, new_line)
            
            log(f"Injected /app directories into {dockerfile_path}: {sorted_dirs}")
        
        # Write modified Dockerfile
        modified_content = '\n'.join(lines)
        with open(dockerfile_path, 'w', encoding='utf-8') as f:
            f.write(modified_content)
        
        return dockerfile_path

    def generate_dockerfile(self, service_name: str, service_config: Dict[str, Any]) -> str:
        """
        Determine the Dockerfile path for a given service.

        Logic:
        - If the service configuration specifies a 'dockerfile', use it.
        - Otherwise, use the default `Dockerfile.<service_name>`.
        - Return None if the Dockerfile does not exist.

        Args:
            service_name (str): Name of the service.
            service_config (dict): Configuration dictionary for the service.

        Returns:
            str | None: Path to the Dockerfile if it exists, else None.

        Example:
            generate_dockerfile("api", {"dockerfile": "Dockerfile.api"}) -> "Dockerfile.api"
        """
        dockerfile_path = service_config.get("dockerfile")
        if not dockerfile_path:
            dockerfile_path = TempStorage.get_dockerfiles_path(self.user) / f"Dockerfile.{service_name}"

        # Return as string if exists
        dockerfile_path = Path(dockerfile_path)
        return str(dockerfile_path) if dockerfile_path.exists() else None



    def build_images(self, 
                    environment: str = None,
                    push_to_registry: bool = False,
                    service_name: str = None,
                    credentials: dict = None):
        """
        Build Docker images for all enabled services in parallel.
        
        Logic:
        1. Check Docker availability.
        2. Iterate over environments (or a specific one if `environment` is provided).
        3. Collect all services that need building.
        4. Build all service images in parallel using ThreadPoolExecutor.
        5. Push images to registry in parallel if requested (with authentication).
        
        Args:
            environment (str, optional): Environment to build images for. Defaults to all.
            push_to_registry (bool, optional): Whether to push built images to Docker registry. Defaults to False.
            service_name (str, optional): The service to build. Defaults to all.
            credentials (dict, optional): Credentials dictionary for registry auth. Keys:
                - registry_url: Registry URL (default: docker.io or .env)
                - registry_username: Registry username
                - registry_password: Registry password/token
        
        Returns:
            bool: True if build process completed successfully.
        
        Example:
            # Using your credentials from .env
            build_images(environment="dev", push_to_registry=True)
            
            # Using client's credentials
            build_images(
                environment="prod", 
                push_to_registry=True,
                credentials={
                    'registry_url': 'client-registry.io',
                    'registry_username': 'client_user',
                    'registry_password': 'client_token'
                }
            )
        """
        if not DockerExecuter.check_docker():
            log("Docker is not available. Please ensure Docker is installed and running.")
            return False

        log(f"Building images (push={push_to_registry})...")
        Logger.start()

        # Get registry credentials (provided or from .env)
        registry_url, registry_username, registry_password = self._get_registry_credentials(credentials)

        # Collect all build tasks
        build_tasks = []
        
        for env in self.deployment_configurer.get_environments():
            if environment is None or environment == env:
                for sn, service_config in self.deployment_configurer.get_services(env).items():
                    if service_name and service_name != sn:
                        continue

                    if service_config.get("disabled", False):
                        log(f"Skipping disabled service: {sn}")
                        continue

                    if service_config.get("skip_build", False):
                        log(f"Skipping build for service (skip_build=True): {sn}")
                        continue

                    # Skip if no dockerfile specified (prebuilt image)
                    if not service_config.get("dockerfile") and not service_config.get("dockerfile_content"):
                        log(f"No dockerfile specified for {sn}, skipping build (using prebuilt image)")
                        continue

                    # Add to build queue
                    build_tasks.append({
                        'env': env,
                        'service_name': sn,
                        'service_config': service_config
                    })

        if not build_tasks:
            log("No services to build")
            Logger.end()
            return True

        log(f"Building {len(build_tasks)} service(s) in parallel...")

        # Build all images in parallel
        from concurrent.futures import ThreadPoolExecutor
        
        def build_single_image(task):
            env = task['env']
            sn = task['service_name']
            service_config = task['service_config']
            
            try:
                # Generate or use dockerfile
                dockerfile_path = self._get_dockerfile_path(sn, service_config)
                
                if not dockerfile_path:
                    log(f"No Dockerfile found for {sn}")
                    return False
                
                # Get build context
                build_context = service_config.get("build_context", ".")
                
                # Generate image name
                docker_hub_user = self.deployment_configurer.get_docker_hub_user()
                version = self._get_version()
                image = ResourceResolver.get_image_name(
                    docker_hub_user, self.user, self.project_name, env, sn, version
                )
                
                # Build image
                log(f"Building {sn} -> {image}")
                DockerExecuter.build_image(
                    dockerfile_path=dockerfile_path,
                    tag=image,
                    context_dir=build_context
                )
                
                log(f"✓ Built {image}")
                return image
                
            except Exception as e:
                log(f"✗ Failed to build {sn}: {e}")
                return None

        # Build in parallel
        built_images = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(build_single_image, task) for task in build_tasks]
            
            for future in futures:
                result = future.result()
                if result:
                    built_images.append(result)

        # Push images if requested
        if push_to_registry and built_images:
            # Login before push
            if registry_username and registry_password:
                log(f"Logging into {registry_url or 'docker.io'} for push...")
                DockerExecuter.docker_login(registry_username, registry_password, registry_url or 'docker.io')
            
            # Push all images in parallel
            log(f"Pushing {len(built_images)} image(s) to registry in parallel...")
            
            def push_single_image(image):
                try:
                    log(f"Pushing {image}...")
                    DockerExecuter.push_image(image)
                    log(f"✓ Pushed {image}")
                    return True
                except Exception as e:
                    log(f"✗ Failed to push {image}: {e}")
                    return False
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                push_futures = [executor.submit(push_single_image, img) for img in built_images]
                
                for future in push_futures:
                    future.result()
            
            # Logout after push (security cleanup)
            if registry_username:
                DockerExecuter.docker_logout(registry_url or 'docker.io')
        
        Logger.end()
        log(f"✓ Build complete: {len(built_images)}/{len(build_tasks)} succeeded")
        return len(built_images) == len(build_tasks)

    def _build_single_image(self, env: str, service_name: str, service_config: Dict[str, Any]) -> tuple:
        """
        Build a single Docker image.
        
        Args:
            env: Environment name
            service_name: Service name
            service_config: Service configuration
            
        Returns:
            Tuple of (tag, success) where tag is the image tag and success is boolean
        """
        try:
            # Handle git checkout if git_repo specified
            build_context = service_config.get("build_context", ".")
            
            if service_config.get("git_repo"):                
                log(f"Checking out Git repository for {service_name}...")
                
                # Get git_token from service config or environment
                git_token = service_config.get("git_token", os.getenv('GIT_TOKEN'))
                
                # Decrypt token if it exists
                if git_token:
                    try:
                        git_token = Encryption.decrypt(git_token)  # DECRYPT HERE
                    except Exception as e:
                        log(f"Warning: Could not decrypt git_token, trying as plain text: {e}")
                        # If decryption fails, try using it as-is (backward compatibility)
                
                
                checkout_path = GitManager.checkout_repo(
                    repo_url=service_config["git_repo"],
                    user=self.user,
                    project_name=self.project_name,
                    service_name=service_name,
                    env=env,
                    git_token=git_token  # Pass token here
                )
                
                if not checkout_path:
                    log(f"Error: Failed to checkout repository for {service_name}")
                    return (None, False)
                
                # Override build_context to use the checkout path
                build_context = checkout_path
                log(f"✓ Using Git checkout: {build_context}")
            
            # Handle dockerfile_content vs dockerfile (EXISTING LOGIC - NO CHANGES)
            dockerfile = None
            if service_config.get("dockerfile_content"):
                # Generate dockerfile from content
                dockerfile_content = self.create_temporary_dockerfile(
                    service_config["dockerfile_content"], 
                    service_name
                )
                dockerfile = self.write_temporary_dockerfile(dockerfile_content, service_name, env)
            elif service_config.get("dockerfile"):
                # Use existing dockerfile path
                dockerfile = self.generate_dockerfile(service_name, service_config)
                if dockerfile:
                    # Still inject app directories for file-based dockerfiles
                    dockerfile = self.inject_app_directories_to_dockerfile(dockerfile, service_name)
            
            if not dockerfile or not os.path.exists(dockerfile):
                log(f"Error: Could not create/find Dockerfile for {service_name}")
                return (None, False)

            docker_hub_user = self.deployment_configurer.get_docker_hub_user()
            version = self._get_version()
            
            tag = ResourceResolver.get_image_name(
                docker_hub_user,
                self.user,
                self.project_name,
                env,
                service_name,
                version
            )

            log(f"Building {service_name}: {tag}")
            log(f"  Build context: {build_context}")
            log(f"  Dockerfile: {dockerfile}")
            
            DockerExecuter.build_image(
                dockerfile_path=dockerfile,
                tag=tag,
                context_dir=build_context,
                progress="plain"
            )
            
            return (tag, True)
            
        except Exception as e:
            log(f"Build error for {service_name}: {e}")
            return (None, False)
    
    def _batch_prepare_servers(
        self,        
        env: str,
        services: Dict[str, Dict[str, Any]],
        target_servers: List[str]
    ):
        """
        Prepare all servers for all services in parallel using batched SSH operations.
        
        NOW INCLUDES: Nginx sidecar setup for service mesh!
        
        Instead of:
            For each service:
                For each server:
                    SSH: Create 3 directories (3 SSH calls)
                    SSH: Create 4 volumes (4 SSH calls)
        
        Do:
            For each server (in parallel):
                SSH ONCE: Create all directories + all volumes + nginx for all services
        
        This reduces SSH overhead from (services × servers × 7 calls) to (servers × 1 call).
        
        Args:            
            env: Environment name
            services: Dictionary of {service_name: service_config} (flat dict from get_services_by_startup_order)
            target_servers: List of server IPs to prepare
        """
        if not services or not target_servers:
            log("No services or servers to prepare")
            return
            
        log(f"Batch preparing {len(target_servers)} server(s) for {len(services)} service(s)...")
        Logger.start()
        
        def prepare_single_server(server_ip):
            """Prepare one server for all services in a single SSH session"""
            try:
                commands = []
                services_prepared = []
                
                # ========================================
                # STEP 1: NGINX SIDECAR SETUP (NEW!)
                # ========================================
                nginx_commands = [
                    # Create nginx directories
                    "mkdir -p /etc/nginx/stream.d",
                    "mkdir -p /etc/nginx/conf.d",
                ]
                
                commands.extend(nginx_commands)
                
                # ========================================
                # STEP 2: SERVICE DIRECTORIES AND VOLUMES
                # ========================================
                # Collect all directory and volume operations for services that will be deployed to this server
                for service_name, service_config in services.items():
                    # Skip localhost services
                    zone = service_config.get("server_zone", "lon1")
                    if zone == "localhost":
                        continue
                    
                    # Skip scheduled services (they handle their own directory/volume creation)
                    if service_config.get("schedule"):
                        continue
                    
                    # Check if this service will be deployed to this server
                    # (we'll prepare all servers for all services to keep it simple)
                    services_prepared.append(service_name)
                    
                    # Host directories (config, secrets, files)
                    host_mount_types = ["config", "secrets", "files"]
                    host_paths = []
                    for path_type in host_mount_types:
                        host_path = PathResolver.get_volume_host_path(
                            self.user, self.project_name, env, service_name, path_type, server_ip
                        )
                        host_paths.append(host_path)
                    commands.append(f"mkdir -p {' '.join(host_paths)}")
                    
                    # Docker volumes (data, logs, backups, monitoring)
                    docker_volume_types = ["data", "logs", "backups", "monitoring"]
                    for path_type in docker_volume_types:
                        volume_name = PathResolver.get_docker_volume_name(
                            self.user, self.project_name, env, path_type, service_name
                        )
                        # Use || true to ignore errors if volume already exists
                        commands.append(f"docker volume create {volume_name} 2>/dev/null || true")
                
                if not commands:
                    log(f"[{server_ip}] No preparation needed (no services for this server)")
                    return (server_ip, True, 0)
                
                # ========================================
                # STEP 3: EXECUTE ALL IN ONE SSH SESSION
                # ========================================
                # Execute all commands in a single SSH session
                batch_cmd = " && ".join(commands)
                
                log(f"[{server_ip}] Preparing {len(services_prepared)} service(s): {', '.join(services_prepared[:3])}{'...' if len(services_prepared) > 3 else ''}")
                CommandExecuter.run_cmd(batch_cmd, server_ip, self.user)
                
                # ========================================
                # STEP 3.5: CREATE NGINX.CONF SEPARATELY (FIX!)
                # ========================================
                # Write nginx.conf using run_cmd_with_stdin to avoid heredoc issues
                nginx_conf_content = """user nginx;
    worker_processes auto;
    error_log /var/log/nginx/error.log warn;
    pid /var/run/nginx.pid;

    events {
        worker_connections 1024;
    }

    stream {
        include /etc/nginx/stream.d/*.conf;
    }

    http {
        include /etc/nginx/mime.types;
        default_type application/octet-stream;
        sendfile on;
        keepalive_timeout 65;
        include /etc/nginx/conf.d/*.conf;
    }"""
                
                try:
                    CommandExecuter.run_cmd_with_stdin(
                        "cat > /etc/nginx/nginx.conf",
                        nginx_conf_content.encode('utf-8'),
                        server_ip,
                        self.user
                    )
                except Exception as e:
                    log(f"[{server_ip}] Warning: Could not create nginx.conf: {e}")
                
                # ========================================
                # STEP 4: START NGINX CONTAINER
                # ========================================
                # Check if nginx is already running                
                network_name = ResourceResolver.get_network_name()
                
                # Ensure network exists first
                try:
                    DockerExecuter.create_network(network_name, server_ip, self.user, ignore_if_exists=True)
                except:
                    pass
                
                # Get ALL services for this env (not just ones being deployed) 
                all_services = self.deployment_configurer.get_services(env)

                # Start/ensure nginx container
                try:
                    NginxConfigGenerator.ensure_nginx_container(
                        project=self.project_name,
                        env=env,
                        services=all_services,
                        target_server=server_ip,
                        user=self.user
                    )
                    log(f"[{server_ip}] Nginx sidecar ready")
                except Exception as e:
                    log(f"[{server_ip}] Warning: Could not start nginx sidecar: {e}")
                
                log(f"✓ [{server_ip}] Completed {len(commands)} operations")
                return (server_ip, True, len(commands))
                
            except Exception as e:
                log(f"✗ [{server_ip}] Preparation failed: {e}")
                return (server_ip, False, 0)
        
        # Prepare all servers in parallel
        total_operations = 0
        successful_servers = 0
        failed_servers = 0
        
        with ThreadPoolExecutor(max_workers=min(len(target_servers), 10)) as executor:
            futures = {
                executor.submit(prepare_single_server, server_ip): server_ip
                for server_ip in target_servers
            }
            
            for future in as_completed(futures):
                server_ip = futures[future]
                try:
                    _, success, operations = future.result()
                    total_operations += operations
                    if success:
                        successful_servers += 1
                    else:
                        failed_servers += 1
                except Exception as e:
                    log(f"✗ Exception preparing {server_ip}: {e}")
                    failed_servers += 1
        
        Logger.end()
        log(f"Batch preparation complete: {successful_servers} servers prepared, "
            f"{failed_servers} failed, {total_operations} total operations")
        
        if failed_servers > 0:
            log(f"Warning: {failed_servers} servers failed preparation - deployment may fail")



    def deploy(self, env: str = None, service_name: str = None, build: bool = True, target_version: str = None, credentials: dict = None) -> bool:
        """
        Deploy services with immutable infrastructure and parallel execution.
        
        Args:
            env: Environment to deploy
            service_name: Specific service (None = all services)
            build: Whether to build images first
            target_version: Override version for rollback (None = use config version)
            credentials: Credentials dictionary (optional)
                Keys: registry_url, registry_username, registry_password, git_token,
                  digitalocean_token, cloudflare_token, cloudflare_email,
                  postgres_password, redis_password, opensearch_password
        
        Behavior:
        - deploy(env="dev")              → build dev + deploy dev
        - deploy(env="dev", build=False) → deploy dev only (no build)
        - deploy()                       → build all + deploy all
        - deploy(build=False)            → deploy all without build
        - deploy(env="dev", target_version="v1.2.3", build=False) → rollback
        
        Uses immutable infrastructure with parallel deployment:
        - Services with same startup_order deploy in parallel
        - Each service deploys to multiple droplets in parallel
        """       
        
        # Store target version for use in image generation
        self._override_version = target_version
        
        # Build phase (if requested)
        if build:
            log("Building images...")
            Logger.start()
            
            # Auto-detect if we need to push to registry
            is_remote = self._has_remote_servers(env)
            if is_remote:
                log("Remote servers detected - images will be pushed to registry")
            
            build_success = self.build_images(environment=env, push_to_registry=is_remote, 
                                  service_name=service_name, credentials=credentials)
            Logger.end()
            
            if not build_success:
                log("Build failed - stopping deployment")
                return False
            
            log("Build complete")
        
        log(f'Deploying {self.project_name}, env: {env or "all"}, service: {service_name or "all"}')
        Logger.start()

        self.pre_provision_servers(env, service_name, credentials=credentials)

        if env:
            self._write_deployment_config(env)

        log("Auto-sync: Pushing config, secrets, and files...")
        environments = [env] if env else self.deployment_configurer.get_environments()
        
        for environment in environments:
            # Get all servers that will receive deployments
            all_servers = ServerInventory.list_all_servers(credentials=credentials)
            
            # Filter to servers in zones used by this environment
            target_ips = []
            for svc_name, svc_config in self.deployment_configurer.get_services(environment).items():
                zone = svc_config.get("server_zone", "lon1")
                if zone != "localhost":
                    zone_servers = [s['ip'] for s in all_servers if s['zone'] == zone]
                    target_ips.extend(zone_servers)
            
            # Remove duplicates
            target_ips = list(set(target_ips))
            
            if target_ips:
                log(f"Pushing to {len(target_ips)} servers: {target_ips}")
                DeploymentSyncer.push(self.user, self.project_name, environment, targets=target_ips)
            else:
                log("No remote servers found for push")

        # Sync inventory with DigitalOcean before deployment
        log("Syncing server inventory with DigitalOcean...")
        ServerInventory.sync_with_digitalocean()

        services = self.get_services_by_startup_order(env)
        
        # Filter to specific service if requested
        if service_name:
            services = {k: v for k, v in services.items() if k == service_name}
            if not services:
                log(f"Service '{service_name}' not found")
                Logger.end()
                return False

        # ========== OPTIMIZATION: BATCH PREPARE + PRE-PULL ==========
        # Get all target servers
        all_target_servers = set()
        for svc_name, svc_config in services.items():
            zone = svc_config.get("server_zone", "lon1")
            if zone != "localhost":
                servers_count = svc_config.get("servers_count", 1)
                all_servers = ServerInventory.list_all_servers(credentials=credentials)
                zone_servers = [s['ip'] for s in all_servers if s['zone'] == zone]
                all_target_servers.update(zone_servers[:servers_count])
        
        if all_target_servers:
            # First: Create all directories and volumes in batch
            self._batch_prepare_servers(env or 'dev', services, list(all_target_servers))
            
            # Second: Pre-pull all images in parallel
            self._pre_pull_images_parallel(services, list(all_target_servers), env or 'dev')
        # ========== END OPTIMIZATIONS ==========

        # PARALLEL SERVICE DEPLOYMENT BY STARTUP ORDER
        services_by_order = defaultdict(list)
        for svc_name, config in services.items():
            order = config.get('startup_order', 5)
            services_by_order[order].append((svc_name, config))
        
        all_success = True
        
        for order in sorted(services_by_order.keys()):
            service_group = services_by_order[order]
            log(f"\n{'='*60}")
            log(f"Deploying startup_order {order}: {[s[0] for s in service_group]}")
            log(f"{'='*60}")
            
            if len(service_group) == 1:
                # Single service - deploy directly
                svc_name, config = service_group[0]
                log(f'Deploying {svc_name}')
                Logger.start()
                
                if self.is_service_scheduled(config):
                    log(f"{svc_name} is scheduled - using simple deployment")
                    
                    zone = config.get("server_zone", "lon1")
                    cpu = config.get("server_cpu", 1)
                    memory = config.get("server_memory", 1024)
                    servers_count = config.get("servers_count", 1)
                    
                    existing_servers = ServerInventory.get_servers(
                        deployment_status=ServerInventory.STATUS_ACTIVE,
                        zone=zone,
                        cpu=cpu,
                        memory=memory
                    )
                    
                    target_servers = [s['ip'] for s in existing_servers[:servers_count]]
                    
                    log(f"Installing scheduled service {svc_name} on {len(target_servers)} servers: {target_servers}")

                    with ThreadPoolExecutor(max_workers=min(len(target_servers), 5)) as executor:
                        futures = []
                        for server_ip in target_servers:
                            future = executor.submit(
                                self.install_scheduled_service,
                                env or 'dev', svc_name, config, server_ip
                            )
                            futures.append(future)
                        
                        # Wait for all to complete
                        for future in as_completed(futures):
                            future.result()
                else:
                    success = self._deploy_immutable(env or 'dev', svc_name, config)
                    
                    if not success:
                        log(f"Deployment failed for {svc_name}")
                        Logger.end()
                        all_success = False
                        break
                
                Logger.end()
                log(f'{svc_name} deployed')
                
            else:
                # Multiple services - deploy in parallel
                log(f"Deploying {len(service_group)} services in parallel...")
                
                service_futures = {}
                with ThreadPoolExecutor(max_workers=min(len(service_group), 5)) as executor:
                    for svc_name, config in service_group:
                        future = executor.submit(
                            self._deploy_single_service,
                            env or 'dev', svc_name, config, credentials
                        )
                        service_futures[future] = svc_name
                    
                    # Wait for all to complete and check results
                    for future in as_completed(service_futures):
                        svc_name = service_futures[future]
                        try:
                            result = future.result()
                            if not result['success']:
                                log(f"Deployment failed for {svc_name}: {result.get('error', 'Unknown error')}")
                                all_success = False
                        except Exception as e:
                            log(f"Deployment exception for {svc_name}: {e}")
                            all_success = False
                
                if not all_success:
                    log("One or more services failed in this startup order - stopping deployment")
                    break
            
            # Deploy backups for this startup_order
            self._deploy_backups_for_startup_order(env or 'dev', dict(service_group))
        
        # Cleanup empty servers
        log("Performing server cleanup...")
        self._cleanup_empty_servers(env=env or 'dev', credentials=credentials)
        
        # Check for nginx automation
        log("Checking for nginx automation...")
        self._setup_nginx_automation(env or 'dev', services)
        
        Logger.end()
        
        if all_success:
            log("Deployment complete")
        else:
            log("Deployment completed with errors")
        
        return all_success

    def _pre_pull_images_parallel(self, services: Dict[str, Dict[str, Any]], target_servers: List[str], env: str):
        """
        Pre-pull all required images to all target servers in parallel.
        This dramatically speeds up deployment by pulling images once before deployment starts.
        
        Args:
            services: Dictionary of service configurations
            target_servers: List of server IPs to pull images to
            env: Environment name
        """
        log(f"Pre-pulling images to {len(target_servers)} server(s) in parallel...")
        Logger.start()
        
        # Collect all unique images that need to be pulled
        images_to_pull = {}  # service_name -> image
        
        for service_name, service_config in services.items():
            # Skip localhost services
            zone = service_config.get("server_zone", "lon1")
            if zone == "localhost":
                continue
            
            # Get image name
            if service_config.get("image"):
                image = service_config["image"]
            else:
                # Skip if no dockerfile (prebuilt images that don't need pulling)
                if not service_config.get("dockerfile") and not service_config.get("dockerfile_content"):
                    continue
                
                docker_hub_user = self.deployment_configurer.get_docker_hub_user()
                version = self._get_version()
                project_name = self.deployment_configurer.get_project_name()
                image = ResourceResolver.get_image_name(
                    docker_hub_user, self.user, project_name, env, service_name, version
                )
            
            images_to_pull[service_name] = image
        
        if not images_to_pull:
            log("No images to pre-pull")
            Logger.end()
            return
        
        # Create pull tasks (image × server combinations)
        pull_tasks = []
        for service_name, image in images_to_pull.items():
            for server_ip in target_servers:
                pull_tasks.append({
                    'image': image,
                    'server_ip': server_ip,
                    'service_name': service_name
                })
        
        log(f"Pulling {len(images_to_pull)} unique image(s) to {len(target_servers)} server(s) = {len(pull_tasks)} pull operations")
        
        # Pull all images in parallel (limit to 10 concurrent to avoid overwhelming network)
        successful_pulls = 0
        failed_pulls = 0
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(
                    DockerExecuter.pull_image,
                    task['image'],
                    task['server_ip'],
                    self.user
                ): task
                for task in pull_tasks
            }
            
            for future in as_completed(futures):
                task = futures[future]
                try:
                    future.result()
                    log(f"✓ Pulled {task['service_name']} to {task['server_ip']}")
                    successful_pulls += 1
                except Exception as e:
                    log(f"✗ Failed to pull {task['service_name']} to {task['server_ip']}: {e}")
                    failed_pulls += 1
        
        Logger.end()
        log(f"Image pre-pull complete: {successful_pulls} succeeded, {failed_pulls} failed")
        
        if failed_pulls > 0:
            log(f"Warning: {failed_pulls} image pulls failed - deployment may be slower")

    def _setup_nginx_automation(self, env: str, services: Dict[str, Dict[str, Any]]) -> None:
        """
        Automatically setup nginx with SSL/DNS for services that have a domain configured.
        Ensures nginx container is running on the same Docker network as services.
        """
        # Load environment variables
        cf_token = os.getenv("CLOUDFLARE_API_TOKEN")
        email = os.getenv("ADMIN_EMAIL", 'robinworld.contact@gmail.com')
        admin_ip = os.getenv("ADMIN_IP")
        
        # Convert "auto" to None for auto-detection
        if admin_ip and admin_ip.lower() == "auto":
            admin_ip = None
        
        # Check each service for domain configuration
        for service_name, service_config in services.items():
            domain = service_config.get("domain")
            if not domain:
                continue
            
            log(f"Service {service_name} has domain: {domain}")
            
            # Determine target server
            servers = service_config.get('servers', ['localhost'])
            target_server = servers[0] if servers else 'localhost'
            
            # CRITICAL: Ensure Docker network exists before nginx container
            self.create_containers_network(env, target_server)
            
            # Localhost: self-signed only (no DNS/firewall)
            if target_server == 'localhost':
                log(f"Setting up nginx for {service_name} on localhost (self-signed)")
                Logger.start()
                try:                    
                    NginxConfigGenerator.setup_service(
                        project=self.project_name,
                        env=env,
                        service_name=service_name,
                        service_config=service_config,
                        target_server='localhost',
                        email=None,  # force self-signed
                        cloudflare_api_token=None,
                        auto_firewall=False  # skip firewall on localhost
                    ) 
                    log(f"Nginx setup complete for {service_name}")
                except Exception as e:
                    log(f"Warning: Nginx setup failed for {service_name}: {e}")
                Logger.end()
                
            # Remote: full automation if credentials available
            elif cf_token and email:
                log(f"Setting up nginx for {service_name} on {target_server} (Let's Encrypt DNS-01 + Cloudflare)")
                Logger.start()
                try:                 
                    NginxConfigGenerator.setup_service(
                        project=self.project_name,
                        env=env,
                        service_name=service_name,
                        service_config=service_config,
                        target_server=target_server,
                        email=email,
                        cloudflare_api_token=cf_token,
                        admin_ip=admin_ip,
                        auto_firewall=True
                    ) 
                    log(f"Nginx setup complete for {service_name}")
                except Exception as e:
                    log(f"Warning: Nginx setup failed for {service_name}: {e}")
                Logger.end()
                
            elif email:
                # Has email but no Cloudflare token - standalone LE
                log(f"Setting up nginx for {service_name} on {target_server} (Let's Encrypt standalone)")
                Logger.start()
                try:                 
                    NginxConfigGenerator.setup_service(
                        project=self.project_name,
                        env=env,
                        service_name=service_name,
                        service_config=service_config,
                        target_server=target_server,
                        email=email,
                        cloudflare_api_token=None,
                        admin_ip=admin_ip,
                        auto_firewall=True
                    ) 
                    log(f"Nginx setup complete for {service_name}")
                except Exception as e:
                    log(f"Warning: Nginx setup failed for {service_name}: {e}")
                Logger.end()
                
            else:
                log(f"Skipping nginx setup for {service_name} (no CLOUDFLARE_EMAIL in .env)")
                log(f"  Add credentials to .env file to enable automatic SSL/DNS setup")
            
    # =============================================================================
    # NEW: TOGGLE DEPLOYMENT HELPERS
    # =============================================================================

    def _determine_toggle(
        self,       
        env: str,
        service: str,
        server_ip: str,
        base_port: int,
        base_name: str
    ) -> Dict[str, Any]:
        """
        Determine which port/name to use based on what's currently running.
        
        Toggle logic:
        - If nothing running → use base (port 8357, name "base")
        - If base running → use secondary (port 18357, name "base_secondary")
        - If secondary running → use base (port 8357, name "base")
        
        Args:           
            env: Environment
            service: Service name
            server_ip: Target server IP
            base_port: Base host port (e.g., 8357)
            base_name: Base container name
            
        Returns:
            {"port": 8357, "name": "base_name"} or
            {"port": 18357, "name": "base_name_secondary"}
        """
        existing = DockerExecuter.find_service_container(self.user, self.project_name, env, service, server_ip)
        
        if not existing:
            # First deployment - use base
            log(f"First deployment of {service} on {server_ip} - using base")
            return {"port": base_port, "name": base_name}
        
        # Toggle logic based on what's currently running
        if existing.get("port") == base_port or existing.get("port") is None:
            # Currently on base (or no port mapping) - toggle to secondary
            log(f"Toggle: {service} on {server_ip} currently on base → deploying secondary")
            return {"port": base_port + 10000, "name": f"{base_name}_secondary"}
        else:
            # Currently on secondary - toggle back to base
            log(f"Toggle: {service} on {server_ip} currently on secondary → deploying base")
            return {"port": base_port, "name": base_name}

    def _determine_backend_mode_for_service(
        self,
        service: str,
        deployed_servers: List[str],
        current_server: str  # NEW: which server's nginx are we configuring?
    ) -> str:
        """
        Determine nginx backend mode FOR THIS SPECIFIC SERVER.
        
        CRITICAL: Mode is PER-SERVER, not global!
        
        Decision:
        - Service deployed on THIS server → "single_server" (use container names via Docker DNS)
        - Service deployed on OTHER servers → "multi_server" (use remote IP:internal_port)
        
        Example:
            Postgres on Server A only
            Current server = A → "single_server" (use postgres container name)
            Current server = B → "multi_server" (use Server A IP:5228)
        
        Args:
            project: Project name
            env: Environment
            service: Service name
            deployed_servers: All servers where THIS SERVICE is deployed
            current_server: The server whose nginx we're configuring
            
        Returns:
            "single_server" or "multi_server"
        """
        # Is the service deployed on THIS server?
        if current_server in deployed_servers:
            log(f"[{current_server}] Backend mode for {service}: single_server (service is local)")
            return "single_server"
        else:
            log(f"[{current_server}] Backend mode for {service}: multi_server (service is remote)")
            return "multi_server"


    def _write_deployment_config(self, env: str):
            """
            Write deployment config to local directory for health monitor.
            
            Uses centralized constants and PathResolver for proper path resolution.
            Config is synced to servers automatically via DeploymentSyncer.push_directory().
            
            Args:
                env: Environment name
            """
            try:
                # Import constants with proper fallback
                try:
                    from .deployment_constants import DEPLOYMENT_CONFIG_SERVICE_NAME, DEPLOYMENT_CONFIG_FILENAME
                except ImportError:
                    from deployment_constants import DEPLOYMENT_CONFIG_SERVICE_NAME, DEPLOYMENT_CONFIG_FILENAME
                
                # Get local config path using centralized constants
                config_dir = PathResolver.get_volume_host_path(
                    self.user,
                    self.project_name,
                    env,
                    DEPLOYMENT_CONFIG_SERVICE_NAME,  # From constants - no hardcoding!
                    "config",
                    "localhost"  # Goes to bastion before being pushed
                )
                
                config_file = Path(config_dir) / DEPLOYMENT_CONFIG_FILENAME  # From constants!
                config_file.parent.mkdir(parents=True, exist_ok=True)
                
                # Build full config
                full_config = {
                    "project": {
                        "name": self.project_name,
                        "docker_hub_user": self.deployment_configurer.get_docker_hub_user(),
                        "version": self._get_version(),
                        "user": self.user
                    },
                    "env": env,
                    "services": self.deployment_configurer.get_services(env)
                }
                
                config_file.write_text(json.dumps(full_config, indent=2), encoding='utf-8')
                log(f"✓ Wrote deployment config: {config_file}")
                
            except Exception as e:
                log(f"⚠️ Failed to write deployment config: {e}")
                # Don't fail deployment - health monitor falls back to nginx configs

    def _generate_nginx_backends(
        self,
        mode: str,        
        env: str,
        service: str,
        deployed_servers: List[str],
        current_server: str  # NEW: which server's nginx are we configuring?
    ) -> List[Dict[str, Any]]:
        """
        Generate backend list for nginx based on deployment mode.
        
        CRITICAL: Backends are different per server!
        
        Args:
            mode: "single_server" or "multi_server"
            project: Project name
            env: Environment
            service: Service name
            deployed_servers: All servers where service is deployed
            current_server: The server whose nginx we're configuring
            
        Returns:
            Single-server: [{"container_name": "...", "port": "5432"}, ...]
            Multi-server: [{"ip": "...", "port": 5228}, ...]  # Internal port!
        """
        if mode == "single_server":
            # Use container names (Docker DNS) - service is on THIS server
            backends = []
            
            # Get both primary and secondary containers if they exist
            for suffix in ["", "_secondary"]:
                container_name = ResourceResolver.get_container_name(self.user, self.project_name, env, service)
                if suffix:
                    container_name = f"{container_name}{suffix}"
                
                # Check if container exists on THIS server
                if DockerExecuter.container_exists(container_name, current_server):
                    # Get container port (not host port!)
                    service_config = self.deployment_configurer.get_services(env)[service]
                    dockerfile = service_config.get("dockerfile")
                    container_ports = ResourceResolver.get_container_ports(service, dockerfile)
                    container_port = container_ports[0] if container_ports else "5432"
                    
                    backends.append({
                        "container_name": container_name,
                        "port": container_port  # Container port (5432, not 8357)
                    })
            
            return backends
        
        else:  # multi_server
            # Use remote server IP + INTERNAL PORT (nginx port, not container port!)
            backends = []
            
            # Calculate the internal port that nginx listens on
            internal_port = ResourceResolver.get_internal_port(self.user, self.project_name, env, service)
            
            for server_ip in deployed_servers:
                # Point to the remote server's nginx internal port
                backends.append({
                    "ip": server_ip,
                    "port": internal_port  # Internal port (5228), not host port!
                })
            
            return backends


    def _update_all_nginx_for_service(
        self,      
        env: str,
        service: str,
        deployed_servers: List[str],
        all_zone_servers: List[Dict[str, Any]]
    ) -> None:
        """
        Update nginx stream config on all servers in zone FOR THIS SERVICE.
        
        CRITICAL: Each server gets a DIFFERENT config based on whether service is local or remote!
        
        Example:
            Postgres deployed on Server A only
            API deployed on Server A and Server B
            
            Server A nginx config:
                upstream new_project_uat_postgres {
                    server new_project_uat_postgres:5432;  # Local via Docker DNS
                }
            
            Server B nginx config:
                upstream new_project_uat_postgres {
                    server 161.35.164.134:5228;  # Remote via IP:internal_port
                }
        
        Args:            
            env: Environment
            service: Service name
            deployed_servers: Servers where THIS SERVICE is deployed
            all_zone_servers: All servers in the zone (for nginx updates)
        """
        if not self._is_tcp_service(service):
            log(f"Skipping nginx stream config for {service} (not a TCP service)")
            return
        
        log(f"Updating nginx stream config for {service} on {len(all_zone_servers)} servers")
        
        # Calculate internal port (stable for this service)
        internal_port = ResourceResolver.get_internal_port(self.user, self.project_name, env, service)
        log(f"Internal port for {service}: {internal_port}")
        
        # Update nginx on EVERY server in the zone (each gets different config!)
        for server in all_zone_servers:
            server_ip = server['ip']
            
            try:
                # Determine mode FOR THIS SPECIFIC SERVER
                mode = self._determine_backend_mode_for_service(
                    service, deployed_servers, server_ip
                )
                
                # Generate backends FOR THIS SPECIFIC SERVER
                backends = self._generate_nginx_backends(
                    mode, env, service, deployed_servers, server_ip
                )
                
                if not backends:
                    log(f"[{server_ip}] No backends found for {service}")
                    continue
                
                # Update nginx config on this server with its specific backends
                NginxConfigGenerator.update_stream_config_on_server(
                    server_ip, self.project_name, env, service, backends, internal_port, mode, user=self.user
                )
                
                log(f"[{server_ip}] Updated nginx config for {service} (mode: {mode}, backends: {len(backends)})")
                
            except Exception as e:
                log(f"Warning: Failed to update nginx on {server_ip}: {e}")

    def _is_tcp_service(self, service_name: str) -> bool:
        """
        Check if service needs TCP proxying (nginx stream).
        
        Args:
            service_name: Service name
            
        Returns:
            True if service needs TCP stream proxying
        """
        tcp_services = ["postgres", "redis", "mongo", "mysql", "rabbitmq", "kafka", "opensearch", "elasticsearch"]
        return service_name.lower() in tcp_services

    def _get_all_servers_in_zone(self, zone: str) -> List[Dict[str, Any]]:
        """
        Get all servers in a zone (both active and reserve).
        
        Args:
            zone: Zone name
            
        Returns:
            List of server dicts
        """
        # Get both active AND reserve servers in the zone
        active = ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_ACTIVE,
            zone=zone
        )
        
        reserve = ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_RESERVE,
            zone=zone
        )
        
        # Combine and deduplicate by IP
        all_servers = {s['ip']: s for s in (active + reserve)}
        return list(all_servers.values())

    def _should_map_host_port(self, deployed_servers: List[str]) -> bool:
        """
        Determine if service needs host port mapping.
        
        Single-server deployment: NO port mapping (use Docker DNS)
        Multi-server deployment: YES port mapping (cross-network communication)
        
        Args:
            deployed_servers: Servers where THIS SERVICE is deployed
            
        Returns:
            True if host port mapping needed
        """
        return len(deployed_servers) > 1

    def _get_opposite_container_name(self, current_name: str, base_name: str) -> Optional[str]:
        """
        Get the opposite container name for cleanup after toggle.
        
        Args:
            current_name: Name of the newly deployed container
            base_name: Base container name
            
        Returns:
            Name of the old container to stop, or None
            
        Examples:
            _get_opposite_container_name("myproj_dev_api", "myproj_dev_api")
            → "myproj_dev_api_secondary"
            
            _get_opposite_container_name("myproj_dev_api_secondary", "myproj_dev_api")
            → "myproj_dev_api"
        """
        if current_name == base_name:
            # New is base, old is secondary
            return f"{base_name}_secondary"
        else:
            # New is secondary, old is base
            return base_name

    # =============================================================================
    # CORE DEPLOYMENT WITH TOGGLE LOGIC
    # =============================================================================

    def _deploy_single_service(
        self,       
        env: str,
        svc_name: str,
        config: Dict[str, Any],
        credentials: dict = None
    ) -> Dict[str, Any]:
        """
        Deploy a single service (called in parallel with other services of same startup_order).
        
        Returns:
            Dict with 'success' (bool) and optional 'error' (str)
        """
        result = {'success': False, 'error': None}
        
        try:
            log(f'[{svc_name}] Starting deployment')
            Logger.start()
            
            if self.is_service_scheduled(config):
                log(f"[{svc_name}] Scheduled service - using simple deployment")
                
                zone = config.get("server_zone", "lon1")
                cpu = config.get("server_cpu", 1)
                memory = config.get("server_memory", 1024)
                servers_count = config.get("servers_count", 1)
                
                existing_servers = ServerInventory.get_servers(
                    deployment_status=ServerInventory.STATUS_ACTIVE,
                    zone=zone,
                    cpu=cpu,
                    memory=memory
                )
                
                target_servers = [s['ip'] for s in existing_servers[:servers_count]]
                
                log(f"[{svc_name}] Installing on {len(target_servers)} servers: {target_servers}")
                
                with ThreadPoolExecutor(max_workers=min(len(target_servers), 5)) as executor:
                    futures = []
                    for server_ip in target_servers:
                        future = executor.submit(
                            self.install_scheduled_service,
                            env or 'dev', svc_name, config, server_ip
                        )
                        futures.append(future)
                    
                    # Wait for all to complete
                    for future in as_completed(futures):
                        future.result()  # This will raise any exceptions that occurred
                
                result['success'] = True
                
            else:
                success = self._deploy_immutable(env, svc_name, config, credentials)
                
                if success:
                    result['success'] = True
                else:
                    result['error'] = 'Immutable deployment failed'
            
            Logger.end()
            
            if result['success']:
                log(f'[{svc_name}] Deployment complete')
            
        except Exception as e:
            log(f'[{svc_name}] Deployment exception: {e}')
            result['error'] = str(e)
            Logger.end()
        
        return result

    def _deploy_to_single_server(
            self,          
            env: str,
            service_name: str,
            service_config: Dict[str, Any],
            target_ip: str,
            base_name: str,
            base_port: int,
            need_port_mapping: bool
        ) -> Dict[str, Any]:
            """
            Deploy service to a single server (called in parallel for each server).
            
            NOTE: Directories and volumes are already created by _batch_prepare_servers()
            NOTE: Images are already pulled by _pre_pull_images_parallel()
            
            This method now only handles:
            1. Network setup
            2. Container deployment
            3. Health check
            4. Old container cleanup
            
            Args:               
                env: Environment name
                service_name: Service name
                service_config: Service configuration
                target_ip: Target server IP
                base_name: Base container name
                base_port: Base host port
                need_port_mapping: Whether to map host ports
                
            Returns:
                Dict with 'success' (bool), 'new_container_name' (str), and optional 'error' (str)
            """
            result = {
                'success': False,
                'new_container_name': None,
                'error': None
            }
            
            try:
                log(f"[{target_ip}] Starting deployment")
                
                # STEP 1: Create network (quick operation)
                self.create_containers_network(env, target_ip)
                network_name = ResourceResolver.get_network_name()
                log(f"[{target_ip}] Network ready")
                
                # STEP 2: Determine toggle (which container name/port to use)
                toggle = self._determine_toggle(env, service_name, target_ip, base_port, base_name)
                new_name = toggle["name"]
                new_port = toggle["port"]
                
                # CRITICAL FIX: Clean up opposite container BEFORE deploying
                old_name = self._get_opposite_container_name(new_name, base_name)
                if old_name:
                    log(f"[{target_ip}] Checking for old container: {old_name}")
                    try:
                        remove_cmd = f"docker rm -f {old_name} 2>/dev/null || true"
                        CommandExecuter.run_cmd(remove_cmd, target_ip, self.user)
                        log(f"[{target_ip}] Ensured {old_name} is removed")
                    except Exception as e:
                        log(f"[{target_ip}] Note: Could not remove old container {old_name}: {e}")
                
                if new_name == base_name:
                    log(f"[{target_ip}] First deployment of {service_name} - using base")
                else:
                    log(f"[{target_ip}] Toggle deployment - using {'base' if new_port == base_port else 'secondary'}")
                
                log(f"[{target_ip}] Using container name: {new_name}, port: {new_port}")
                
                result['new_container_name'] = new_name
                
                # Also remove target container if it exists (from failed previous deployment)
                try:
                    DockerExecuter.stop_and_remove_container(new_name, target_ip, ignore_if_not_exists=True)
                    log(f"[{target_ip}] Removed existing {new_name} if present")
                except:
                    pass

                # STEP 3: Get volumes (directories already created by _batch_prepare_servers)
                volumes = PathResolver.generate_all_volume_mounts(
                    self.user, self.project_name, env, service_name, target_ip,
                    use_docker_volumes=True,
                    auto_create_dirs=False  # Already created in batch!
                )
                
                # STEP 4: Get environment variables
                env_vars = service_config.get("env_vars", {})
                
                # STEP 5: Build image name
                image = service_config.get("image")
                if not image:
                    docker_hub_user = self.deployment_configurer.get_docker_hub_user()
                    version = self._get_version()
                    image = ResourceResolver.get_image_name(
                        docker_hub_user, self.user, self.project_name, env, service_name, version
                    )
                
                # STEP 6: Get ports configuration
                if need_port_mapping:
                    # Multi-server: use toggle port
                    ports_config = service_config.get("ports", [])
                    if not ports_config:
                        dockerfile = service_config.get("dockerfile")
                        container_ports = ResourceResolver.get_container_ports(service_name, dockerfile)
                        ports_config = container_ports if container_ports else ["8000"]
                    
                    # Map internal port to toggle port
                    port_mappings = {}
                    for internal_port in ports_config:
                        port_mappings[str(new_port)] = str(internal_port)
                else:
                    # Single-server: no port mapping
                    port_mappings = {}
                
                # STEP 7: Start container
                DockerExecuter.run_container(
                    name=new_name,
                    image=image,
                    ports=port_mappings if port_mappings else None,
                    volumes=volumes,
                    environment=env_vars,
                    network=network_name,
                    server_ip=target_ip,
                    user=self.user,
                    restart_policy='always' if service_config.get('restart', False) else 'no'
                )
                
                log(f"[{target_ip}] Container {new_name} started successfully")
                
                # STEP 8: Health check (if applicable)
                health_check_enabled = service_config.get('health_check', True)
                if health_check_enabled:
                    log(f"[{target_ip}] Running health check...")
                    health_ok = self._verify_container_health(
                        service_name, service_config, new_name, target_ip
                    )
                    if not health_ok:
                        raise Exception("Health check failed")
                    log(f"[{target_ip}] Health check passed")
                
                result['success'] = True
                log(f"[{target_ip}] Deployment successful")
                
            except Exception as e:
                result['error'] = str(e)
                log(f"[{target_ip}] Deployment failed: {e}")
                
                # Cleanup on failure
                if result.get('new_container_name'):
                    try:
                        DockerExecuter.stop_and_remove_container(
                            result['new_container_name'],
                            target_ip,
                            ignore_if_not_exists=True
                        )
                    except:
                        pass
            
            return result

    def _verify_container_health(
        self,
        service_name: str,
        service_config: Dict[str, Any],
        container_name: str,
        server_ip: str
    ) -> bool:
        """
        Verify container health after deployment.
        
        For TCP services (postgres, redis): check if container is running
        For HTTP services: optionally check health endpoint
        For one-time jobs: check exit code
        """
        # Get ports to determine service type
        dockerfile = service_config.get("dockerfile")
        container_ports = ResourceResolver.get_container_ports(service_name, dockerfile)
        
        if not container_ports:
            # No ports - likely a one-time job
            log(f"[{server_ip}] No ports detected - checking container status")
            time.sleep(0.5)
            
            try:
                # First check if container is still running
                status_result = CommandExecuter.run_cmd(
                    f"docker ps --filter 'name={container_name}' --format '{{{{.Status}}}}'",
                    server_ip, self.user
                )
                status = status_result.stdout.strip() if hasattr(status_result, 'stdout') else str(status_result).strip()
                
                if status and 'Up' in status:
                    # Container still running - treat as success (long-running job)
                    log(f"[{server_ip}] Container still running - treating as healthy")
                    return True
                
                exit_code = DockerExecuter.get_container_exit_code(container_name, server_ip, self.user)

                if exit_code == -1:
                    log(f"[{server_ip}] Could not determine exit code for {container_name}")
                    # Get logs to show why
                    self._log_container_failure(container_name, server_ip)
                    return False
                elif exit_code in [0, 1, 2, 3]:
                    log(f"[{server_ip}] One-time job completed successfully (exit code {exit_code})")
                    return True
                else:
                    log(f"[{server_ip}] One-time job failed (exit code {exit_code})")
                    # Get logs to show why
                    self._log_container_failure(container_name, server_ip)
                    return False
                        
            except Exception as e:
                log(f"[{server_ip}] Health check exception: {e}")
                return False
                
        else:
            # TCP service - check if container is running
            log(f"[{server_ip}] TCP service - verifying container is running")
            time.sleep(2)  # Give container time to fully start

            try:
                # Use docker ps -a to catch all states
                result = CommandExecuter.run_cmd(
                    f"docker ps -a --filter 'name={container_name}' --format '{{{{.Status}}}}'",
                    server_ip, self.user
                )
                status = result.stdout.strip() if hasattr(result, 'stdout') else str(result).strip()
                
                if not status:
                    log(f"[{server_ip}] Container not found")
                    self._log_container_failure(container_name, server_ip)
                    return False
                
                status_lower = status.lower()
                
                # Check for good state
                if 'up' in status_lower and 'restarting' not in status_lower:
                    log(f"[{server_ip}] TCP service container running: {status}")
                    return True
                else:
                    log(f"[{server_ip}] Container in bad state: {status}")
                    self._log_container_failure(container_name, server_ip)
                    return False
                    
            except Exception as e:
                log(f"[{server_ip}] Could not check container status: {e}")
                return False

    def _log_container_failure(self, container_name: str, server_ip: str, lines: int = 100):
        """
        Log container failure details including logs and inspect output.
        IMPROVED: Multiple log retrieval methods + intelligent filtering.
        """
        log(f"[{server_ip}] ═══════════════════════════════════════════════")
        log(f"[{server_ip}] Container '{container_name}' failure details:")
        log(f"[{server_ip}] ═══════════════════════════════════════════════")
        
        try:
            # Get container status
            inspect_result = CommandExecuter.run_cmd(
                f"docker inspect {container_name} --format '{{{{.State.Status}}}}' 2>/dev/null || echo 'not found'",
                server_ip, self.user
            )
            status = inspect_result.stdout.strip() if hasattr(inspect_result, 'stdout') else str(inspect_result).strip()
            log(f"[{server_ip}] Status: {status}")
            
            # Get exit code
            exit_code_result = CommandExecuter.run_cmd(
                f"docker inspect {container_name} --format '{{{{.State.ExitCode}}}}' 2>/dev/null || echo '-1'",
                server_ip, self.user
            )
            exit_code = exit_code_result.stdout.strip() if hasattr(exit_code_result, 'stdout') else str(exit_code_result).strip()
            log(f"[{server_ip}] Exit Code: {exit_code}")
            
            # Get error message from State.Error
            error_result = CommandExecuter.run_cmd(
                f"docker inspect {container_name} --format '{{{{.State.Error}}}}' 2>/dev/null || echo ''",
                server_ip, self.user
            )
            error_msg = error_result.stdout.strip() if hasattr(error_result, 'stdout') else str(error_result).strip()
            if error_msg:
                log(f"[{server_ip}] Error: {error_msg}")
            
            log(f"[{server_ip}] Container logs (last {lines} lines):")
            log(f"[{server_ip}] ───────────────────────────────────────────────")
            
            # Try multiple methods to get logs
            logs_result = None
            
            # METHOD 1: Standard docker logs
            try:
                result = CommandExecuter.run_cmd(
                    f"docker logs --tail {lines} {container_name} 2>&1",
                    server_ip, self.user
                )
                logs_result = result.stdout if hasattr(result, 'stdout') else str(result)
            except:
                pass
            
            # METHOD 2: With timestamps
            if not logs_result or not logs_result.strip():
                try:
                    result = CommandExecuter.run_cmd(
                        f"docker logs --timestamps --tail {lines} {container_name} 2>&1",
                        server_ip, self.user
                    )
                    logs_result = result.stdout if hasattr(result, 'stdout') else str(result)
                except:
                    pass
            
            # METHOD 3: Direct log file access
            if not logs_result or not logs_result.strip():
                try:
                    id_result = CommandExecuter.run_cmd(
                        f"docker inspect {container_name} --format '{{{{.Id}}}}' 2>/dev/null",
                        server_ip, self.user
                    )
                    container_id = id_result.stdout.strip() if hasattr(id_result, 'stdout') else ""
                    
                    if container_id and len(container_id) > 10:
                        log_file = f"/var/lib/docker/containers/{container_id}/{container_id}-json.log"
                        result = CommandExecuter.run_cmd(
                            f"tail -n {lines} {log_file} 2>&1",
                            server_ip, self.user
                        )
                        logs_result = result.stdout if hasattr(result, 'stdout') else str(result)
                except:
                    pass
            
            # METHOD 4: Check all containers with same name
            if not logs_result or not logs_result.strip():
                try:
                    ps_result = CommandExecuter.run_cmd(
                        f"docker ps -a --filter 'name={container_name}' --format '{{{{.ID}}}}' --no-trunc",
                        server_ip, self.user
                    )
                    output = ps_result.stdout if hasattr(ps_result, 'stdout') else str(ps_result)
                    container_ids = [cid.strip() for cid in output.strip().split('\n') if cid.strip()]
                    
                    for cid in container_ids:
                        try:
                            result = CommandExecuter.run_cmd(
                                f"docker logs --tail {lines} {cid} 2>&1",
                                server_ip, self.user
                            )
                            temp_logs = result.stdout if hasattr(result, 'stdout') else str(result)
                            if temp_logs and temp_logs.strip():
                                logs_result = temp_logs
                                break
                        except:
                            continue
                except:
                    pass
            
            # Process logs with intelligent filtering
            if logs_result:
                logs = logs_result.strip() if isinstance(logs_result, str) else str(logs_result).strip()
                
                # Split into lines for processing
                all_lines = logs.split('\n')
                
                # Define noise patterns (package installation, build output)
                noise_patterns = [
                    'fetch https://dl-cdn.alpinelinux.org',
                    'fetch http://dl-cdn.alpinelinux.org',
                    '/alpine/v3',
                    'Installing ',
                    'Executing busybox',
                    'OK: ',
                    'MiB in ',
                    'packages'
                ]
                
                # Define error patterns (what we WANT to see)
                error_patterns = [
                    'Error',
                    'ERROR',
                    'Exception',
                    'Traceback',
                    'ImportError',
                    'ModuleNotFoundError',
                    'AttributeError',
                    'Failed',
                    'FAILED',
                    'traceback',
                    'File "',
                    'SyntaxError',
                    'NameError',
                    'KeyError',
                    'ValueError',
                    'TypeError',
                    'ConnectionError',
                    'TimeoutError',
                    'permission denied',
                    'cannot',
                    'fatal',
                    'FATAL',
                    'EXIT CODE:'
                ]
                
                # Separate lines into categories
                error_lines = []
                clean_lines = []
                
                for line in all_lines:
                    # Check if it's an error line (PRIORITY)
                    if any(pattern in line for pattern in error_patterns):
                        error_lines.append(line)
                    # Check if it's noise
                    elif any(pattern in line for pattern in noise_patterns):
                        continue  # Skip noise
                    # Otherwise keep it
                    else:
                        clean_lines.append(line)
                
                # Show error lines first (most important)
                if error_lines:
                    log(f"[{server_ip}] ⚠️  ERROR LINES DETECTED:")
                    for line in error_lines[-50:]:  # Last 50 error lines
                        log(f"[{server_ip}] ❌ {line}")
                    log(f"[{server_ip}] ───────────────────────────────────────────────")
                
                # Then show clean lines (non-error, non-noise)
                if clean_lines:
                    log(f"[{server_ip}] Other relevant logs:")
                    for line in clean_lines[-30:]:  # Last 30 clean lines
                        log(f"[{server_ip}] {line}")
                
                # If we filtered everything out, show raw logs
                if not error_lines and not clean_lines:
                    log(f"[{server_ip}] Showing raw logs (all filtered as noise):")
                    for line in all_lines[-30:]:
                        log(f"[{server_ip}] {line}")
            else:
                log(f"[{server_ip}] No logs available from any method")
                
        except Exception as e:
            log(f"[{server_ip}] Could not get container logs: {e}")
        
        log(f"[{server_ip}] ═══════════════════════════════════════════════")

    def _cleanup_empty_servers(self, env: str, credentials: dict = None):
            """
            Find servers with no services deployed and destroy/release them.
            
            This implements the cleanup phase of your plan:
            "Find all IPs where no service is deployed and destroy/put them back to reserve"
            
            A server is considered "empty" only if:
            1. No running containers for this project/env, AND
            2. No scheduled cron jobs for this project/env (excluding health_monitor)
            
            Args:                
                env: Environment name
            """       
            all_servers = ServerInventory.list_all_servers(credentials=credentials)
            container_pattern = f"{self.project_name}_{env}_"
            
            empty_servers = []
            
            for server in all_servers:
                server_ip = server['ip']
                
                try:
                    # Check 1: Running containers
                    result = CommandExecuter.run_cmd(
                        f"docker ps --filter 'name={container_pattern}' --format '{{{{.Names}}}}'",
                        server_ip,
                        self.user
                    )
                    
                    # Extract container names properly
                    if hasattr(result, 'stdout'):
                        output = result.stdout.strip()
                    else:
                        output = str(result).strip()
                    
                    # CRITICAL FIX: Filter out garbage lines
                    containers = [
                        c.strip() 
                        for c in output.split('\n') 
                        if c.strip() and c.strip().startswith(container_pattern)
                    ]
                    
                    if containers:
                        log(f"Server {server_ip} has {len(containers)} running container(s)")
                        continue  # Server has containers, keep it
                    
                    # Check 2: Scheduled cron jobs (excluding health_monitor)
                    cron_jobs = CronManager.list_managed_cron_jobs(
                        user=self.user,
                        project=self.project_name,
                        env=env,
                        server_ip=server_ip
                    )
                    
                    # Filter out health_monitor jobs (those are system-level, not project-specific)
                    project_cron_jobs = [
                        job for job in cron_jobs 
                        if 'health_monitor' not in job.lower()
                    ]
                    
                    if project_cron_jobs:
                        log(f"Server {server_ip} has {len(project_cron_jobs)} scheduled job(s) - keeping")
                        continue  # Server has scheduled jobs, keep it
                    
                    # Server is truly empty
                    empty_servers.append(server_ip)
                    log(f"Server {server_ip} has no {self.project_name}/{env} services (containers or cron jobs)")
                        
                except Exception as e:
                    log(f"Could not check {server_ip}: {e}")
                    continue
            
            if empty_servers:
                # Check if we should destroy or just move to reserve
                # Use keep_reserve setting from config
                destroy_empty = not self.deployment_configurer.raw_config.get('project', {}).get('keep_reserve', False)
                
                if destroy_empty:
                    log(f"Destroying {len(empty_servers)} empty servers: {empty_servers}")
                    ServerInventory.release_servers(empty_servers, destroy=True, credentials=credentials)
                else:
                    log(f"Returning {len(empty_servers)} empty servers to reserve pool: {empty_servers}")
                    ServerInventory.release_servers(empty_servers, destroy=False, credentials=credentials)
            else:
                log("No empty servers found")

    def _get_servers_running_service(self, env: str, service_name: str, credentials: Dict=None) -> List[str]:
        """
        Get list of server IPs that have containers for this service.
        
        Args:           
            env: Environment name
            service_name: Service name
            credentials: Optional Dict of credentials
            
        Returns:
            List of server IPs that have containers for this service
        """
        all_servers = ServerInventory.list_all_servers(credentials=credentials)
        servers_with_service = []
        
        container_pattern = f"{self.project_name}_{env}_{service_name}"
        
        for server in all_servers:
            server_ip = server['ip']
            try:
                # Check if this server has containers for this service
                result = CommandExecuter.run_cmd(
                    f"docker ps -a --filter 'name={container_pattern}' --format '{{{{.Names}}}}'",
                    server_ip,
                    self.user
                )
                
                # Extract container names properly
                if hasattr(result, 'stdout'):
                    output = result.stdout.strip()
                else:
                    output = str(result).strip()
                
                # CRITICAL FIX: Filter out garbage lines
                containers = [
                    c.strip() 
                    for c in output.split('\n') 
                    if c.strip() and c.strip().startswith(container_pattern)
                ]
                
                if containers:
                    servers_with_service.append(server_ip)
                    
            except Exception as e:
                log(f"Could not check containers on {server_ip}: {e}")
                continue
        
        return servers_with_service

    def _cleanup_service_on_server(self, env: str, service_name: str, server_ip: str):
        """
        Stop and remove all containers for a service on a specific server.
        
        Args:            
            env: Environment name
            service_name: Service name
            server_ip: Server IP to clean up
        """
        container_pattern = f"{self.project_name}_{env}_{service_name}"
        
        try:
            # Find all containers for this service (base and secondary)
            result = CommandExecuter.run_cmd(
                f"docker ps -a --filter 'name={container_pattern}' --format '{{{{.Names}}}}'",
                server_ip,
                self.user
            )
            
            # Extract container names - handle both stdout attribute and string result
            if hasattr(result, 'stdout'):
                output = result.stdout.strip()
            else:
                output = str(result).strip()
            
            # CRITICAL FIX: Filter out garbage lines that don't match pattern
            containers = [
                c.strip() 
                for c in output.split('\n') 
                if c.strip() and c.strip().startswith(container_pattern)
            ]
            
            for container_name in containers:
                try:
                    DockerExecuter.stop_and_remove_container(
                        container_name,
                        server_ip,
                        ignore_if_not_exists=True
                    )
                    log(f"Removed container {container_name} from {server_ip}")
                except Exception as e:
                    log(f"Could not remove {container_name} from {server_ip}: {e}")
            
            NginxConfigGenerator.cleanup_service_nginx_config(self.project_name, env, service_name, server_ip, user=self.user)
                    
        except Exception as e:
            log(f"Could not cleanup {service_name} on {server_ip}: {e}")

    def _deploy_immutable(
                self,                
                env: str,
                service_name: str,
                service_config: Dict[str, Any],
                credentials: dict = None
            ) -> bool:
                """
                Deploy with immutable infrastructure - MATCHES YOUR PLAN 100%.
                
                Your Plan Implementation:
                
                1) If not dedicated servers, green_ips = get up to server_counts current greens
                else green_ips = []
                2) If shortfall, create new servers (new_ips) - instant via pre-provisioning
                3) todel_ips = get all IPs running service but NOT in target list
                4) target_ips = green_ips + new_ips
                5) success = True
                6) For each IP in target_ips:
                a) Deploy: if service not in IP, deploy normal; else toggle port
                b) Start service
                c) Check health
                d) Update nginx with new target_port
                e) If fail: stop/delete new container, success=False, break
                    else: stop/delete previous container
                7) For IP in todel_ips: stop and delete the container
                
                Cleanup: Find all IPs with no services and destroy/reserve them
                
                Returns:
                    True if deployment successful
                """
                # Apply client credentials for password provisioning if provided
                if credentials:
                    log(f"[{service_name}] Applying client credentials...")
                    self.deployment_configurer.rebuild_config(credentials)

                log(f"Immutable deployment for {service_name}")
                Logger.start()
                
                green_ips = [] 
                new_ips = []

                # Get server requirements
                zone = service_config.get("server_zone", "lon1")
                
                # LOCALHOST SPECIAL CASE
                if zone == "localhost":
                    log(f"Localhost deployment - starting service directly")
                    self.create_containers_network(env, 'localhost')
                    
                    base_name = ResourceResolver.get_container_name(self.user, self.project_name, env, service_name)
                    dockerfile = service_config.get("dockerfile")
                    container_ports = ResourceResolver.get_container_ports(service_name, dockerfile)
                    container_port = container_ports[0] if container_ports else "8000"
                    base_port = ResourceResolver.get_host_port(self.user, self.project_name, env, service_name, container_port)
                    
                    toggle = self._determine_toggle(env, service_name, 'localhost', base_port, base_name)
                    new_name = toggle["name"]   
                    
                    old_name = self._get_opposite_container_name(new_name, base_name)
                    if old_name:
                        try:
                            DockerExecuter.stop_and_remove_container(old_name, target_ip, ignore_if_not_exists=True)
                        except:
                            pass

                    # Also remove target container if it exists (from failed previous deployment)
                    try:
                        DockerExecuter.stop_and_remove_container(new_name, target_ip, ignore_if_not_exists=True)
                    except:
                        pass

                    try:
                        self.start_service(env, service_name, service_config, 'localhost')
                        all_zone_servers = [{'ip': 'localhost'}]
                        self._update_all_nginx_for_service(
                            self.project_name, env, service_name, ['localhost'], all_zone_servers
                        )
                        Logger.end()
                        log(f"Localhost deployment successful")
                        return True
                    except Exception as e:
                        log(f"Localhost deployment failed: {e}")
                        Logger.end()
                        return False
                
                # REMOTE SERVER DEPLOYMENT - Following YOUR PLAN exactly
                
                servers_count = service_config.get("servers_count", 1)
                cpu = service_config.get("server_cpu", 1)
                memory = service_config.get("server_memory", 1024)
                dedicated_servers = service_config.get("dedicated_servers", False)
                
                # STEP 1: 
                if not dedicated_servers:
                    existing_actives = ServerInventory.get_servers(
                        deployment_status=ServerInventory.STATUS_ACTIVE,  
                        zone=zone,
                        cpu=cpu,
                        memory=memory
                    )
                    
                    reuse_count = min(len(existing_actives), servers_count)
                    green_ips = [s['ip'] for s in existing_actives[:reuse_count]]
                    
                    if len(green_ips) == servers_count:
                        log(f"Reusing {len(green_ips)} existing active servers: {green_ips}")
                    else:
                        log(f"Found {len(green_ips)} existing actives, need {servers_count} - will reuse and create {servers_count - len(green_ips)} more")
                else:
                    log(f"dedicated_servers=True - will create new servers")

                # STEP 2: claim_servers already marks as ACTIVE - no changes needed
                if len(green_ips) < servers_count:
                    needed = servers_count - len(green_ips)
                    try:
                        new_ips = ServerInventory.claim_servers(
                            count=needed,
                            zone=zone,
                            cpu=cpu,
                            memory=memory,
                            credentials=credentials
                        )
                        log(f"Created {len(new_ips)} new servers: {new_ips}")
                    except Exception as e:
                        log(f"Failed to claim servers: {e}")
                        Logger.end()
                        return False
                            
                # STEP 3: Calculate todel_ips (servers with service but not in target)
                current_service_servers = self._get_servers_running_service(env, service_name)
                
                # STEP 4: Calculate target_ips
                target_ips = green_ips + new_ips

                
                try:
                    self._batch_prepare_servers(env, {service_name: service_config}, target_ips)
                except Exception as e:
                    log(f"Warning: batch prepare on target_ips failed: {e}")

                # Check if we have any servers
                if not target_ips:
                    log(f"ERROR: No servers available for {service_name} deployment")
                    Logger.end()
                    return False

                todel_ips = [ip for ip in current_service_servers if ip not in target_ips]
                
                if todel_ips:
                    log(f"Will remove {service_name} from these servers: {todel_ips}")
                
                log(f"Target deployment: {target_ips}")
                
                # STEP 5: Set success flag
                success = True
                deployed_servers = []
                
                # Calculate base naming (same for all servers)
                base_name = ResourceResolver.get_container_name(self.user, self.project_name, env, service_name)
                dockerfile = service_config.get("dockerfile")
                container_ports = ResourceResolver.get_container_ports(service_name, dockerfile)
                container_port = container_ports[0] if container_ports else "8000"
                base_port = ResourceResolver.get_host_port(self.user, self.project_name, env, service_name, container_port)
                
                # Determine if we need port mapping (multi-server needs it)
                need_port_mapping = self._should_map_host_port(target_ips)
                
                # STEP 6: Deploy to each IP in target_ips - PARALLELIZED WITH ROLLBACK
                deployment_futures = {}
                deployed_container_names = {}  # Track new container names for rollback
                success = True
                
                with ThreadPoolExecutor(max_workers=min(len(target_ips), 10)) as executor:
                    for target_ip in target_ips:
                        future = executor.submit(
                            self._deploy_to_single_server,
                            env, service_name, service_config,
                            target_ip, base_name, base_port, need_port_mapping
                        )
                        deployment_futures[future] = target_ip
                    
                    # Wait for all deployments and collect results
                    for future in as_completed(deployment_futures):
                        target_ip = deployment_futures[future]
                        try:
                            result = future.result()
                            if result['success']:
                                deployed_servers.append(target_ip)
                                # Track the new container name for potential rollback
                                deployed_container_names[target_ip] = result.get('container_name')
                                log(f"✓ Deployment successful on {target_ip}")
                            else:
                                log(f"✗ Deployment failed on {target_ip}: {result.get('error', 'Unknown error')}")
                                success = False
                        except Exception as e:
                            log(f"✗ Deployment exception on {target_ip}: {e}")
                            success = False
                
                # ROLLBACK: If any deployment failed, cleanup all newly deployed containers
                if not success:
                    log("Deployment failed - cleaning up newly deployed containers")
                    
                    # Only run parallel cleanup if there are containers to clean up
                    if deployed_container_names:
                        cleanup_futures = {}
                        with ThreadPoolExecutor(max_workers=min(len(deployed_container_names), 10)) as executor:
                            for deployed_ip, container_name in deployed_container_names.items():
                                if container_name:
                                    future = executor.submit(
                                        DockerExecuter.stop_and_remove_container,
                                        container_name, deployed_ip, ignore_if_not_exists=True
                                    )
                                    cleanup_futures[future] = (deployed_ip, container_name)
                            
                            # Wait for cleanup to complete
                            for future in as_completed(cleanup_futures):
                                deployed_ip, container_name = cleanup_futures[future]
                                try:
                                    future.result()
                                    log(f"Rolled back container {container_name} on {deployed_ip}")
                                except Exception as e:
                                    log(f"Warning: Could not rollback {container_name} on {deployed_ip}: {e}")
                    else:
                        log("No containers to rollback (all deployments failed before container creation)")
                    
                    # Release newly created servers back to pool
                    if new_ips:
                        log(f"Releasing {len(new_ips)} newly created servers back to pool")
                        ServerInventory.release_servers(new_ips, destroy=False, credentials=credentials)
                    
                    Logger.end()
                    return False
                
                # STEP 6d: Update nginx on ALL servers in zone (only if all deployments succeeded)
                log("All deployments successful - updating nginx configurations")
                
                all_zone_servers = self._get_all_servers_in_zone(zone)
                self._update_all_nginx_for_service(
                    env, service_name, deployed_servers, all_zone_servers
                )
                
                # STEP 7: Cleanup todel_ips (remove service from servers no longer in target)
                if todel_ips:
                    log(f"Cleaning up {service_name} from removed servers: {todel_ips}")
                    for ip in todel_ips:
                        self._cleanup_service_on_server(env, service_name, ip)
                
                # Record deployment state
                DeploymentStateManager.record_deployment(
                    user=self.user,
                    project=self.project_name,
                    env=env,
                    service=service_name,
                    servers=target_ips,
                    container_name=base_name,
                    version=self._get_version()
                )   
                
                Logger.end()
                log(f"Immutable deployment successful")
                return True

    def get_services_by_startup_order(self, env: str = None) -> Dict[str, Dict[str, Any]]:
        """
        Get services sorted by startup_order.
        
        Args:
            env: Environment name (None = all environments)
            
        Returns:
            Ordered dict of services
        """
        if env:
            services = self.deployment_configurer.get_services(env)
        else:
            # Merge all environments
            services = {}
            for environment in self.deployment_configurer.get_environments():
                services.update(self.deployment_configurer.get_services(environment))
        
        # Sort by startup_order (default to 5 if not specified)
        sorted_services = sorted(
            services.items(),
            key=lambda x: x[1].get('startup_order', 5)
        )
        
        return dict(sorted_services)

    def create_containers_network(self, env: str, server_ip: str = 'localhost'):
        """Create Docker network for services communication"""
        network_name = ResourceResolver.get_network_name()
        
        try:
            DockerExecuter.create_network(network_name, server_ip, self.user, ignore_if_exists=True)
            log(f"Network {network_name} ready on {server_ip}")
        except Exception as e:
            log(f"Warning: Could not create network {network_name} on {server_ip}: {e}")

    def start_service(
        self,        
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        server_ip: str = 'localhost'       
    ):
        """
        Start a service container (wrapper for compatibility).
        Determines if long-running or scheduled and calls appropriate method.
        """
        if self.is_service_scheduled(service_config):
            # For scheduled services, install the cron job
            return self.install_scheduled_service(env, service_name, service_config, server_ip)
        else:
            # For long-running services, start the container
            return self.start_long_running_service(env, service_name, service_config, server_ip)

    def start_long_running_service(
        self,        
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        server_ip: str = 'localhost'      
    ) -> bool:
        """
        Start a long-running service container.
        
        This method is called during deployment to start service containers.
        It handles network setup, volume mounting, and container lifecycle.
        """
        try:
            # Create network           
            self.create_containers_network(env, server_ip)
            network_name = ResourceResolver.get_network_name()
            
            # Get container name and image
            base_name = ResourceResolver.get_container_name(self.user, self.project_name, env, service_name)
            
            # Get image (either custom or prebuilt)
            if service_config.get("image"):
                image = service_config["image"]
            else:
                docker_hub_user = self.deployment_configurer.get_docker_hub_user()
                version = self._get_version()
                image = ResourceResolver.get_image_name(
                    docker_hub_user, self.user, self.project_name, env, service_name, version
                )
            
           # Images are pre-pulled in batch prep, no need to pull again
            log(f"Using pre-pulled image {image} on {server_ip}")
            
            # Get volumes
            volumes = PathResolver.generate_all_volume_mounts(
                self.user, self.project_name, env, service_name,
                server_ip=server_ip,
                use_docker_volumes=True                
            )
            
            # Get dockerfile for port detection
            dockerfile = service_config.get("dockerfile")
            container_ports = ResourceResolver.get_container_ports(service_name, dockerfile)
            container_port = container_ports[0] if container_ports else "8000"
            
            # Determine if we need port mapping (based on deployment strategy)
            # For localhost or single-server internal services, no port mapping
            # This will be refined in _deploy_immutable based on actual deployment topology
            ports = None  # Default: no port mapping
            
            # Get environment variables
            env_vars = service_config.get("env_vars", {})
            
            # Get restart policy
            restart_policy = "unless-stopped" if service_config.get("restart", True) else "no"
            
            # Run container
            DockerExecuter.run_container(
                image=image,
                name=base_name,
                network=network_name,
                ports=ports,
                volumes=volumes,
                environment=env_vars,
                restart_policy=restart_policy,
                server_ip=server_ip,
                user=self.user
            )
            
            log(f"Started {service_name} on {server_ip}")
            return True
            
        except Exception as e:
            log(f"Failed to start {service_name} on {server_ip}: {e}")
            return False

    def install_scheduled_service(
                self,                
                env: str,
                service_name: str,
                service_config: Dict[str, Any],
                server_ip: str = 'localhost'
            ):
                """Install a scheduled service using CronManager"""
                schedule = service_config.get("schedule")
                
                if not schedule or not CronManager.validate_cron_schedule(schedule):
                    log(f"Invalid schedule for {service_name}: {schedule}")
                    return False
                
                # Get image
                if service_config.get("image"):
                    image = service_config["image"]
                else:
                    docker_hub_user = self.deployment_configurer.get_docker_hub_user()
                    version = self._get_version()
                    image = ResourceResolver.get_image_name(
                        docker_hub_user, self.user, self.project_name, env, service_name, version
                    )
                
                # Pull image if remote
                if server_ip != 'localhost':
                    log(f"Pulling image {image} to {server_ip}...")
                    DockerExecuter.pull_image(image, server_ip)
                
                # Parallel directory and volume creation (like normal services)
                with ThreadPoolExecutor(max_workers=2) as executor:
                    dir_future = executor.submit(
                        PathResolver.ensure_host_directories,
                        self.user, self.project_name, env, service_name, server_ip
                    )
                    vol_future = executor.submit(
                        PathResolver.ensure_docker_volumes,
                        self.user, self.project_name, env, service_name, server_ip
                    )
                    
                    # Wait for both to complete
                    dir_future.result()
                    vol_future.result()
                
                log(f"[{server_ip}] Directories and volumes ready")
                
                # Install via CronManager directly
                success = CronManager.install_cron_job(
                    user=self.user,
                    project=self.project_name,
                    env=env,
                    service_name=service_name,
                    service_config=service_config,
                    docker_hub_user=self.deployment_configurer.get_docker_hub_user(),
                    version=self._get_version(),
                    server_ip=server_ip
                )
                
                if success:
                    log(f"Installed scheduled service {service_name} on {server_ip}")
                else:
                    log(f"Failed to install scheduled service {service_name} on {server_ip}")
                
                return success

    def wait_for_health_check(
        self,
        url: Optional[str],
        timeout: int = 60,
        service_name: str = ""
    ) -> bool:
        """
        Wait for service to become healthy.
        
        Args:
            url: Health check URL (None = skip HTTP check)
            timeout: Timeout in seconds
            service_name: Service name for logging
            
        Returns:
            True if healthy
        """
        if not url:
            log(f"Skipping HTTP health check for {service_name}")
            return True
        
        log(f"Health checking {service_name} at {url}...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(url, timeout=5)
                if response.status_code == 200:
                    log(f"Health check passed for {service_name}")
                    return True
            except Exception:
                pass
            
            time.sleep(2)
        
        log(f"Health check failed for {service_name} after {timeout}s")
        return False

    def create_temporary_dockerfile(self, dockerfile_content: Dict[str, str], service_name: str) -> str:
        """Generate Dockerfile from dockerfile_content dict"""
        # Sort keys properly
        def sort_key(key):
            parts = key.split('.')
            return [int(part) for part in parts]
        
        sorted_keys = sorted(dockerfile_content.keys(), key=sort_key)
        
        lines = []
        for key in sorted_keys:
            lines.append(dockerfile_content[key])
        
        return '\n'.join(lines)

    def write_temporary_dockerfile(self, content: str, service_name: str, env: str) -> str:
        """Write temporary Dockerfile and inject /app directories"""
        dockerfile_path = TempStorage.get_dockerfile_path(self.user, self.project_name, env, service_name)
        
        # Write initial content
        dockerfile_path.write_text(content)
        
        # Inject /app directories
        return self.inject_app_directories_to_dockerfile(str(dockerfile_path), service_name)

    def _update_nginx_for_new_servers(
        self,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        new_server_ips: List[str]
    ):
        """Update nginx for services that have a domain"""
        domain = service_config.get("domain")
        if not domain:
            return
        
        # Get email and Cloudflare token from env       
        email = os.getenv("ADMIN_EMAIL")
        cloudflare_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
        
        # Update nginx on each new server
        for server_ip in new_server_ips:
            try:    
                NginxConfigGenerator.setup_service(
                    project=self.project_name,
                    env=env,
                    service_name=service_name,
                    service_config=service_config,
                    target_server=server_ip,
                    email=email,
                    cloudflare_api_token=cloudflare_api_token,
                    auto_firewall=True
                ) 
                
                log(f"Updated nginx for {service_name} on {server_ip}")
            except Exception as e:
                log(f"Failed to update nginx on {server_ip}: {e}")

    def list_deployments(self, env: str = None, include_costs: bool = True) -> Dict[str, Any]:
        """
        Get current deployment status.
        
        Args:
            env: Filter by environment
            include_costs: Include cost information from DigitalOcean
            
        Returns:
            Deployment status dictionary
        """ 
        # Get all deployments
        if env:
            deployments = DeploymentStateManager.get_all_services(self.project_name, env)
        else:
            deployments = DeploymentStateManager.get_all_services(self.project_name)
        
        # Enrich with cost data if requested
        if include_costs:
            total_cost = 0
            
            # Get all servers used by this project
            all_servers = set()
            if isinstance(deployments, dict):
                for env_data in deployments.values():
                    if isinstance(env_data, dict):
                        for service_data in env_data.values():
                            if isinstance(service_data, dict):
                                servers = service_data.get("servers", [])
                                all_servers.update(servers)
            
            # Calculate costs
            if all_servers:
                try:
                    cost_tracker = DOCostTracker()
                    for server_ip in all_servers:
                        server_info = ServerInventory.get_server_by_ip(server_ip)
                        if server_info:
                            cost = cost_tracker.calculate_server_cost(
                                server_info['cpu'],
                                server_info['memory']
                            )
                            total_cost += cost
                except Exception as e:
                    log(f"Could not calculate costs: {e}")
            
            deployments['_metadata'] = {
                'total_servers': len(all_servers),
                'monthly_cost_usd': total_cost
            }
        
        return deployments

    def print_deployments(self, env: str = None, include_costs: bool = True):
        """Pretty-print deployment status to console"""
        deployments = self.list_deployments(env, include_costs)
        
        print(f"\n{'='*60}")
        print(f"Deployment Status: {self.project_name}")
        print(f"{'='*60}\n")
        
        # Print metadata if available
        if '_metadata' in deployments:
            meta = deployments.pop('_metadata')
            print(f"Total Servers: {meta.get('total_servers', 0)}")
            if 'monthly_cost_usd' in meta:
                print(f"Monthly Cost: ${meta['monthly_cost_usd']:.2f}")
            print()
        
        # Print deployments by environment
        for env_name, env_data in deployments.items():
            if not isinstance(env_data, dict):
                continue
            
            print(f"Environment: {env_name}")
            print("-" * 40)
            
            for service_name, service_data in env_data.items():
                if not isinstance(service_data, dict):
                    continue
                
                version = service_data.get('version', 'unknown')
                servers = service_data.get('servers', [])
                container_name = service_data.get('container_name', 'unknown')
                deployed_at = service_data.get('deployed_at', 'unknown')
                
                print(f"  Service: {service_name}")
                print(f"    Version: {version}")
                print(f"    Container: {container_name}")
                print(f"    Servers: {len(servers)}")
                for server in servers:
                    print(f"      - {server}")
                print(f"    Deployed: {deployed_at}")
                print()
            
            print()

    def logs(self, service: str, env: str, lines: int = 100) -> str:
        """
        Fetch logs from service containers.
        
        Args:
            service: Service name
            env: Environment
            lines: Number of lines to tail
            
        Returns:
            Log output
        """   
        # Query live infrastructure for running containers
        servers = LiveDeploymentQuery.get_servers_running_service(
            self.project_name, env, service
        )
        
        if not servers:
            return f"No running containers found for {self.project_name}/{env}/{service}"
        
        # Get actual container name from first server
        container = DockerExecuter.find_service_container(
            self.user, self.project_name, env, service, servers[0]
        )
        
        if not container:
            return f"Container not found on {servers[0]}"
        
        container_name = container['name']
        server_ip = servers[0]
        
        try:
            logs = DockerExecuter.get_container_logs(
                container_name, lines, server_ip
            )
            return logs
        except Exception as e:
            return f"Failed to fetch logs: {e}"
    
    def print_logs(self, service: str, env: str, lines: int = 100):
        """Fetch and print logs to console"""
        print(f"\n{'='*60}")
        print(f"Logs: {self.project_name}/{env}/{service}")
        print(f"{'='*60}\n")
        
        logs = self.logs(service, env, lines)
        print(logs)

    def _get_opposite_container_name(self, current_name: str, base_name: str) -> Optional[str]:
        """
        Get the opposite container name for cleanup after toggle.
        
        Args:
            current_name: Name of the newly deployed container
            base_name: Base container name
            
        Returns:
            Name of the old container to stop, or None
            
        Examples:
            _get_opposite_container_name("myproj_dev_api", "myproj_dev_api")
            → "myproj_dev_api_secondary"
            
            _get_opposite_container_name("myproj_dev_api_secondary", "myproj_dev_api")
            → "myproj_dev_api"
        """
        if current_name == base_name:
            # New is base, old is secondary
            return f"{base_name}_secondary"
        else:
            # New is secondary, old is base
            return base_name
        
    def pre_provision_servers(self, env: str, service_name: str = None, credentials: Dict=None) -> Dict[str, List[str]]:
        """
        Pre-provision all servers needed for deployment based on service requirements.
        
        This analyzes all services that will be deployed and provisions all required
        servers upfront in parallel, making the actual deployment much faster.
        
        Args:
            env: Environment to provision for
            service_name: Optional specific service, otherwise all services
            credentials: Optional Dict of secrets
            
        Returns:
            Dictionary mapping "cpu_memory_zone" -> list of provisioned server IPs
            
        Example:
            # Call this before deploy() for faster deployment
            deployer.pre_provision_servers(env="prod")
            deployer.deploy(env="prod")
        """        
        log(f"Pre-provisioning servers for {self.project_name}/{env}")
        
        # Get all services to deploy
        services = self.get_services_by_startup_order(env)
        if service_name:
            services = {k: v for k, v in services.items() if k == service_name}
            if not services:
                log(f"Service '{service_name}' not found")
                return {}
        
        # Calculate all server requirements
        server_requirements = {}  # key: "cpu_memory_zone" -> value: count needed
        
        log("Calculating server requirements...")
        
        for svc_name, svc_config in services.items():
            # Skip localhost services
            zone = svc_config.get("server_zone", "lon1")
            if zone == "localhost":
                continue
            
            # Skip scheduled services (they handle their own servers)
            if self.is_service_scheduled(svc_config):
                continue
            
            # Get server specs
            cpu = svc_config.get("server_cpu", 1)
            memory = svc_config.get("server_memory", 1024)
            servers_count = svc_config.get("servers_count", 1)
            dedicated_servers = svc_config.get("dedicated_servers", False)
            
            # Create requirement key
            key = f"{cpu}_{memory}_{zone}"
            
            # Calculate total servers needed for this spec
            if key in server_requirements:
                if dedicated_servers:
                    # Dedicated servers always add to the count
                    server_requirements[key] += servers_count
                else:
                    # Shared servers - take the max
                    server_requirements[key] = max(server_requirements[key], servers_count)
            else:
                server_requirements[key] = servers_count
            
            log(f"  {svc_name}: {servers_count} x ({cpu}CPU/{memory}MB) in {zone} {'[dedicated]' if dedicated_servers else '[shared]'}")
        
        # Provision all required servers IN PARALLEL
        provisioned_servers = {}
        
        log("\nCalculating provisioning needs...")
        provisioning_tasks = []
        
        # First pass: determine what needs to be created
        for key, required_count in server_requirements.items():
            cpu, memory, zone = key.split('_')
            cpu = int(cpu)
            memory = int(memory)
            
            # Find existing reserve/green servers with matching specs
            existing_reserves = ServerInventory.get_servers(
                deployment_status=ServerInventory.STATUS_RESERVE,
                zone=zone,
                cpu=cpu,
                memory=memory
            )
            
            existing_greens = ServerInventory.get_servers(
                deployment_status=ServerInventory.STATUS_ACTIVE,
                zone=zone,
                cpu=cpu,
                memory=memory  
            )
            
            existing_count = len(existing_reserves) + len(existing_greens)
            existing_ips = [s['ip'] for s in existing_reserves] + [s['ip'] for s in existing_greens]
            
            log(f"  Spec: {cpu}CPU/{memory}MB in {zone}")
            log(f"    Required: {required_count}, Existing: {existing_count}")
            
            # Calculate how many new servers needed
            new_servers_needed = max(0, required_count - existing_count)
            
            if new_servers_needed > 0:
                log(f"    Need to create: {new_servers_needed}")
                provisioning_tasks.append({
                    'key': key,
                    'count': new_servers_needed,
                    'cpu': cpu,
                    'memory': memory,
                    'zone': zone,
                    'existing_ips': existing_ips,
                    'required_total': required_count
                })
            else:
                log(f"    Sufficient servers already exist")
                provisioned_servers[key] = existing_ips[:required_count]
        
        # Create ALL servers in parallel across different specs
        if provisioning_tasks:
            log(f"\nProvisioning {sum(t['count'] for t in provisioning_tasks)} servers in parallel...")
                        
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {}
                
                for task in provisioning_tasks:
                    # Submit each provisioning task
                    future = executor.submit(
                        DOManager.create_servers,
                        count=task['count'],
                        region=task['zone'],
                        cpu=task['cpu'],
                        memory=task['memory'],
                        tags=[
                            ServerInventory.TAG_PREFIX, 
                            f"zone:{task['zone']}", 
                            f"status:{ServerInventory.STATUS_RESERVE}"
                        ],
                        credentials=credentials
                    )
                    futures[future] = task
                
                # Collect results as they complete
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        new_droplets = future.result()
                        new_ips = [d['ip'] for d in new_droplets]
                        log(f"  Created {len(new_ips)} servers for {task['key']}: {new_ips}")
                        
                        # Combine with existing and store
                        all_ips = task['existing_ips'] + new_ips
                        provisioned_servers[task['key']] = all_ips[:task['required_total']]
                        
                    except Exception as e:
                        log(f"  Failed to create servers for {task['key']}: {e}")
                        # Store what we have (existing servers only)
                        provisioned_servers[task['key']] = task['existing_ips'][:task['required_total']]
        
        log(f"\nPre-provisioning complete. Provisioned servers by spec:")
        for key, ips in provisioned_servers.items():
            log(f"  {key}: {len(ips)} servers")
        
        return provisioned_servers
    

# =============================================================================
    # BACKUP DEPLOYMENT METHODS
    # =============================================================================
    
    def _deploy_backups_for_startup_order(
        self,
        env: str,        
        services: Dict[str, Dict[str, Any]]
    ):
        """
        After a startup_order completes, deploy backup services for any
        stateful services in that order.
        
        This ensures backups are deployed immediately after their parent services,
        guaranteeing they run on the same servers.
        
        Args:
            env: Environment name           
            services: Services that were deployed in this order
        """       
        
        project_name = self.deployment_configurer.get_project_name()
        
        for service_name, service_config in services.items():
            service_type = BackupManager.detect_service_type(service_name, service_config)
            
            if not service_type:
                continue
            
            if not BackupManager.is_backup_enabled(service_config):
                continue
            
            log(f"\n[{service_name}] (backup) Deploying backup service...")
            
            # Get servers where parent service was just deployed
            deployed_servers = self._get_deployed_servers(env, service_name)
            
            if not deployed_servers:
                log(f"[{service_name}] (backup) ✗ No deployed servers found for parent")
                continue
            
            log(f"[{service_name}] (backup) Parent deployed on: {deployed_servers}")
            
            # Deploy backup to same servers
            self._deploy_backup_service(               
                env,
                service_name,
                service_config,
                deployed_servers
            )
    
    def _get_deployed_servers(self, env: str, service_name: str) -> List[str]:
        """
        Get list of server IPs where a service is currently deployed.
        
        Args:           
            env: Environment name
            service_name: Service name
            
        Returns:
            List of server IPs where the service is running
        """
        container_pattern = ResourceResolver.get_container_name_pattern(self.user, self.project_name, env, service_name)
        
        all_servers = ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_ACTIVE
        )
        
        deployed_servers = []
        
        for server in all_servers:
            server_ip = server['ip']
            try:
                # Check if this server has containers for this service
                result = CommandExecuter.run_cmd(
                    f"docker ps --filter 'name={container_pattern}' --format '{{{{.Names}}}}'",
                    server_ip,
                    self.user
                )
                
                if result and str(result).strip():
                    deployed_servers.append(server_ip)
                    
            except Exception as e:
                log(f"Could not check {server_ip}: {e}")
                continue
        
        return deployed_servers
    
    def _deploy_backup_service(
            self,            
            env: str,
            parent_service_name: str,
            parent_service_config: Dict[str, Any],
            deployed_servers: List[str]
        ):
            """
            Deploy backup service to the same servers as the parent service.
            This ensures backup can connect via Docker DNS (container name).
            
            Args:             
                env: Environment name
                parent_service_name: Parent service name (e.g., "postgres")
                parent_service_config: Parent service configuration
                deployed_servers: Exact servers where parent is running
            """       
            backup_service_name = f"{parent_service_name}_backup"
            
            # Generate backup service config for first server (for path resolution)
            backup_service_config = BackupManager.generate_backup_service_config(
                self.user, self.project_name, env, parent_service_name, parent_service_config, deployed_servers[0]
            )
            
            if not backup_service_config:
                log(f"[{parent_service_name}] (backup) Could not generate backup config")
                return
            
            # Build image first (only once)
            log(f"[{parent_service_name}] (backup) Building backup image...")
            docker_hub_user = self.deployment_configurer.get_docker_hub_user()
            version = self._get_version()
            
            # Generate Dockerfile
            dockerfile_content_dict = backup_service_config.get("dockerfile_content")
            if dockerfile_content_dict:
                # Convert dict to string content
                dockerfile_content = self.create_temporary_dockerfile(
                    dockerfile_content_dict, 
                    backup_service_name
                )
                
                # Write to temp file (this method exists and handles injection)
                temp_dockerfile = self.write_temporary_dockerfile(
                    dockerfile_content, 
                    backup_service_name, 
                    env
                )
                
                # Build image
                image_name = ResourceResolver.get_image_name(
                    docker_hub_user, self.user, self.project_name, env, backup_service_name, version
                )
                
                build_context = backup_service_config.get("build_context", ".")
                
                log(f"[{parent_service_name}] (backup) Building {image_name}...")
                DockerExecuter.build_image(
                    dockerfile_path=temp_dockerfile,
                    tag=image_name,
                    context_dir=build_context,
                    progress="plain"
                )
                
                # Push if deploying to remote servers
                if deployed_servers and deployed_servers[0] != 'localhost':
                    log(f"[{parent_service_name}] (backup) Pushing {image_name}...")
                    DockerExecuter.push_image(image_name)
            
            # Deploy to all parent's servers in parallel
            log(f"[{parent_service_name}] (backup) Installing on {len(deployed_servers)} server(s)...")
            
            with ThreadPoolExecutor(max_workers=len(deployed_servers)) as executor:
                futures = {}
                
                for server_ip in deployed_servers:
                    future = executor.submit(
                        self._install_backup_on_server,
                        env, backup_service_name, backup_service_config,
                        parent_service_name, server_ip
                    )
                    futures[future] = server_ip
                
                # Collect results
                for future in as_completed(futures):
                    server_ip = futures[future]
                    try:
                        success = future.result()
                        if success:
                            log(f"[{parent_service_name}] (backup) ✓ Installed on {server_ip}")
                        else:
                            log(f"[{parent_service_name}] (backup) ✗ Failed on {server_ip}")
                    except Exception as e:
                        log(f"[{parent_service_name}] (backup) ✗ Exception on {server_ip}: {e}")

    def _install_backup_on_server(
        self,      
        env: str,
        backup_service_name: str,
        backup_service_config: Dict[str, Any],
        parent_service_name: str,
        server_ip: str
    ) -> bool:
        """
        Install backup service on a specific server.
        
        Args:           
            env: Environment name
            backup_service_name: Backup service name
            backup_service_config: Backup service configuration
            parent_service_name: Parent service name (for logging)
            server_ip: Target server IP
            
        Returns:
            True if successful
        """
        try:
            # Use install_scheduled_service method
            success = self.install_scheduled_service(                
                env=env,
                service_name=backup_service_name,
                service_config=backup_service_config,
                server_ip=server_ip
            )
            log(f"Installed backup of {parent_service_name} on {server_ip}")
            return success
            
        except Exception as e:
            log(f"Failed to install backup of {parent_service_name} on {server_ip}: {e}")
            return False

      
# =============================================================================
    # BASTION BACKUP COMMANDS
    # =============================================================================
    
    def pull_backups(
        self,
        env: str,
        service: Optional[str] = None
    ) -> bool:
        """
        Pull backup volumes from servers to bastion.
        
        Args:
            env: Environment name
            service: Optional specific service (e.g., "postgres"), or None for all
            
        Returns:
            True if successful
        """       
        
        log(f"Pulling backups for {self.user}/{self.project_name}/{env}")
        
        # Get all services or specific service
        services = self.deployment_configurer.get_services(env)
        
        if service:
            if service not in services:
                log(f"Service '{service}' not found in {env} environment")
                return False
            services = {service: services[service]}
        
        # Filter to only stateful services       
        stateful_services = {}
        for svc_name, svc_config in services.items():
            service_type = BackupManager.detect_service_type(svc_name, svc_config)
            if service_type:
                stateful_services[svc_name] = svc_config
        
        if not stateful_services:
            log("No stateful services found to pull backups from")
            return False
        
        log(f"Pulling backups for: {list(stateful_services.keys())}")
        
        # Pull backups for each service
        success = True
        for svc_name in stateful_services:
            log(f"\nPulling backups for {svc_name}...")
            
            # Get servers where service is deployed
            deployed_servers = self._get_deployed_servers(env, svc_name)
            
            if not deployed_servers:
                log(f"No servers found for {svc_name}")
                continue
            
            # Pull from first server (backups should be identical)
            server_ip = deployed_servers[0]
            log(f"Pulling from {server_ip}...")
            
            # Pull backups volume
            volume_name = PathResolver.get_docker_volume_name(self.user, self.project_name, env, "backups", svc_name)
            local_path = PathResolver.get_volume_host_path(self.user, self.project_name, env, svc_name, "backups", "localhost")
            
            try:
                # Use docker cp to extract volume contents
                temp_container = f"temp_backup_copy_{svc_name}"
                
                # Create temporary container with volume mounted
                CommandExecuter.run_cmd(
                    f"docker create --name {temp_container} -v {volume_name}:/backups alpine",
                    server_ip,
                    self.user
                )
                
                # Copy files from container to local
                # Create local directory
                Path(local_path).mkdir(parents=True, exist_ok=True)
                
                # Use rsync or scp to pull files
                if server_ip == 'localhost':
                    # Local copy
                    CommandExecuter.run_cmd(
                        f"docker cp {temp_container}:/backups/. {local_path}/",
                        server_ip,
                        self.user
                    )
                else:
                    # Remote copy via docker cp then scp
                    remote_temp = f"/tmp/backups_{svc_name}"
                    CommandExecuter.run_cmd(
                        f"docker cp {temp_container}:/backups {remote_temp}",
                        server_ip,
                        self.user
                    )
                    
                    # SCP from remote to local                   
                    subprocess.run([
                        "scp", "-r",
                        f"{self.user}@{server_ip}:{remote_temp}/.",
                        local_path
                    ], check=True) 
                    
                    # Cleanup remote temp
                    CommandExecuter.run_cmd(
                        f"rm -rf {remote_temp}",
                        server_ip,
                        self.user
                    )
                
                # Remove temporary container
                CommandExecuter.run_cmd(
                    f"docker rm {temp_container}",
                    server_ip,
                    self.user
                )
                
                log(f"✓ Pulled backups for {svc_name} to {local_path}")
                
            except Exception as e:
                log(f"✗ Failed to pull backups for {svc_name}: {e}")
                success = False
        
        return success
    
    def list_backups(
        self,
        env: str,
        service: str
    ) -> List[Dict[str, Any]]:
        """
        List available backups for a service.
        
        Args:
            env: Environment name
            service: Service name (e.g., "postgres")
            
        Returns:
            List of backup info dicts with timestamp, size, age
        """             
        
        # Get local backup path
        local_path = PathResolver.get_volume_host_path(self.user, self.project_name, env, service, "backups", "localhost")
        backup_dir = Path(local_path)
        
        if not backup_dir.exists():
            log(f"No backups found at {backup_dir}")
            log("Run 'pull_backups' first to download backups from servers")
            return []
        
        # Find backup files      
        service_config = self.deployment_configurer.get_services(env).get(service, {})
        service_type = BackupManager.detect_service_type(service, service_config)
        
        if not service_type:
            log(f"Unknown service type for {service}")
            return []
        
        # Get file pattern based on service type
        if service_type == "postgres":
            pattern = "postgres_*.dump"
        elif service_type == "redis":
            pattern = "redis_*.rdb"
        else:
            pattern = "*"
        
        backups = []
        for backup_file in backup_dir.glob(pattern):
            try:
                # Extract timestamp from filename
                timestamp_str = backup_file.stem.replace(f"{service_type}_", "")
                timestamp = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                
                # Get file info
                stat = backup_file.stat()
                size_mb = stat.st_size / (1024 * 1024)
                age_hours = (datetime.now() - timestamp).total_seconds() / 3600
                
                backups.append({
                    "timestamp": timestamp_str,
                    "datetime": timestamp,
                    "filename": backup_file.name,
                    "size_mb": round(size_mb, 2),
                    "age_hours": round(age_hours, 1),
                    "path": str(backup_file)
                })
            except Exception as e:
                log(f"Warning: Could not parse {backup_file.name}: {e}")
        
        # Sort by timestamp (newest first)
        backups.sort(key=lambda x: x["datetime"], reverse=True)
        
        return backups
    
    def rollback(
        self,
        env: str,
        service: str,
        timestamp: str
    ) -> bool:
        """
        Restore service from a backup timestamp.
        
        WARNING: This will stop the service, replace its data, and restart it.
        
        Args:
            env: Environment name
            service: Service name (e.g., "postgres")
            timestamp: Backup timestamp (e.g., "20241016_140530")
            
        Returns:
            True if successful
        """
       
        log(f"WARNING: Rolling back {service} to {timestamp}")
        log("This will STOP the service and REPLACE its data!")
        
        # Verify backup exists locally
        backups = self.list_backups(env, service)
        backup = next((b for b in backups if b["timestamp"] == timestamp), None)
        
        if not backup:
            log(f"Backup {timestamp} not found for {service}")
            log("Available backups:")
            for b in backups:
                log(f"  - {b['timestamp']} ({b['size_mb']} MB, {b['age_hours']} hours ago)")
            return False
        
        log(f"Using backup: {backup['filename']} ({backup['size_mb']} MB)")
        
        # Get deployed servers
        deployed_servers = self._get_deployed_servers(env, service)
        if not deployed_servers:
            log(f"Service {service} is not deployed")
            return False
        
        log(f"Will restore on {len(deployed_servers)} server(s): {deployed_servers}")
        
        # Confirm
        response = input("Type 'yes' to continue: ")
        if response.lower() != 'yes':
            log("Rollback cancelled")
            return False
        
        # Perform rollback on each server       
        service_config = self.deployment_configurer.get_services(env).get(service, {})
        service_type = BackupManager.detect_service_type(service, service_config)
        
        success = True
        for server_ip in deployed_servers:
            log(f"\nRolling back on {server_ip}...")
            
            try:
                # 1. Stop service
                container_name = ResourceResolver.get_container_name(self.user, self.project_name, env, service)
                log(f"Stopping {container_name}...")
                DockerExecuter.stop_and_remove_container(container_name, server_ip, ignore_if_not_exists=True)
                
                # 2. Restore backup based on service type
                if service_type == "postgres":
                    success &= self._rollback_postgres(
                        env, service, backup, server_ip
                    )
                elif service_type == "redis":
                    success &= self._rollback_redis(
                        env, service, backup, server_ip
                    )
                
                # 3. Restart service
                log(f"Restarting {service}...")
                # Trigger redeployment
                self._deploy_immutable(env, service, service_config)
                
            except Exception as e:
                log(f"✗ Rollback failed on {server_ip}: {e}")
                success = False
        
        if success:
            log(f"\n✓ Rollback complete for {service}")
        else:
            log(f"\n✗ Rollback had errors for {service}")
        
        return success
    
    def _rollback_postgres(
        self,        
        env: str,
        service: str,
        backup: Dict[str, Any],
        server_ip: str
    ) -> bool:
        """
        Restore Postgres from backup dump.
        
        Process:
        1. Upload backup file to server
        2. Stop postgres container
        3. Create temp restore container with data volume
        4. Run pg_restore
        5. Cleanup temp container
        
        Args:            
            env: Environment name
            service: Service name (e.g., "postgres")
            backup: Backup info dict with 'path' and 'filename'
            server_ip: Target server IP
            
        Returns:
            True if successful
        """
        try:            
            log(f"Restoring Postgres backup on {server_ip}...")            
             
            # Get container and volume names
            container_name = ResourceResolver.get_container_name(self.user, self.project_name, env, service)
            data_volume = ResourceResolver.get_docker_volume_name(self.user, self.project_name, env, "data", service)
            backups_volume = ResourceResolver.get_docker_volume_name(self.user, self.project_name, env, "backups", service)  

            db_name = ResourceResolver.get_db_name(self.user, self.project_name, env, service)
            db_user = ResourceResolver.get_db_user(self.user, self.project_name, service)            
            
            # Step 1: Upload backup to server's backup volume
            log(f"  Uploading backup file: {backup['filename']}")
            backup_file_path = backup['path']
            
            # Read backup file locally
            with open(backup_file_path, 'rb') as f:
                backup_data = f.read()
            
            # Write to server's backup volume using a temp container
            temp_upload_container = f"restore_upload_{int(time.time())}"
            upload_cmd = (
                f"docker run --rm -i --name {temp_upload_container} "
                f"-v {backups_volume}:/backups "
                f"alpine:latest sh -c 'cat > /backups/{backup['filename']}'"
            )
            
            CommandExecuter.run_cmd_with_stdin(
                upload_cmd, 
                backup_data, 
                server_ip, 
                self.user
            )
            log(f"  ✓ Backup uploaded to server")
            
            # Step 2: Stop postgres container
            log(f"  Stopping {service} container...")
            try:
                DockerExecuter.stop_container(container_name, server_ip, self.user)
                log(f"  ✓ Container stopped")
            except Exception as e:
                log(f"  Warning: Could not stop container (may already be stopped): {e}")
            
            # Step 3: Drop and recreate database using temp container
            log(f"  Recreating database...")
            
            # Get network name for postgres connection
            network = ResourceResolver.get_network_name()
            
            # Get password file path
            secrets_volume = PathResolver.get_volume_host_path(
                self.user, self.project_name, env, service, "secrets", server_ip
            )
            secret_filename = ResourceResolver._get_secret_filename(service)
            password_file = f"{secrets_volume}/{secret_filename}"
            
            # Start postgres temporarily to drop/create DB
            temp_pg_container = f"restore_pg_{int(time.time())}"
            container_secrets_path = PathResolver.get_volume_container_path(service, "secrets")
            secret_filename = ResourceResolver._get_secret_filename(service)
            password_file_env = f"{container_secrets_path}/{secret_filename}"

            start_pg_cmd = (
                f"docker run -d --name {temp_pg_container} "
                f"-v {data_volume}:/var/lib/postgresql/data "
                f"-v {secrets_volume}:{container_secrets_path}:ro "
                f"-e POSTGRES_DB={db_name} "
                f"-e POSTGRES_USER={db_user} "
                f"-e POSTGRES_PASSWORD_FILE={password_file_env} "
                f"--network {network} "
                f"postgres:latest"
            )
            CommandExecuter.run_cmd(start_pg_cmd, server_ip, self.user)
            
            # Wait for postgres to be ready
            log(f"  Waiting for Postgres to start...")
            time.sleep(5)
            
            # Read password
            password_read_cmd = f"cat {password_file}"
            db_password = CommandExecuter.run_cmd(password_read_cmd, server_ip, self.user).strip()
            
            # Drop connections and recreate database
            drop_db_cmd = (
                f"docker exec {temp_pg_container} psql "
                f"-U {db_user} -d postgres "
                f"-c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid();\""
            )
            CommandExecuter.run_cmd(drop_db_cmd, server_ip, self.user)
            
            drop_cmd = (
                f"docker exec {temp_pg_container} psql "
                f"-U {db_user} -d postgres "
                f"-c 'DROP DATABASE IF EXISTS {db_name};'"
            )
            CommandExecuter.run_cmd(drop_cmd, server_ip, self.user)
            
            create_cmd = (
                f"docker exec {temp_pg_container} psql "
                f"-U {db_user} -d postgres "
                f"-c 'CREATE DATABASE {db_name};'"
            )
            CommandExecuter.run_cmd(create_cmd, server_ip, self.user)
            log(f"  ✓ Database recreated")
            
            # Step 4: Run pg_restore
            log(f"  Restoring backup data...")
            
            # Create restore container with access to both data and backups volumes
            temp_restore_container = f"restore_exec_{int(time.time())}"
            restore_cmd = (
                f"docker run --rm --name {temp_restore_container} "
                f"-v {backups_volume}:/backups:ro "
                f"--network {network} "
                f"-e PGPASSWORD={db_password} "
                f"postgres:latest "
                f"pg_restore -h {temp_pg_container} -U {db_user} -d {db_name} "
                f"--no-owner --no-acl /backups/{backup['filename']}"
            )
            
            CommandExecuter.run_cmd(restore_cmd, server_ip, self.user)
            log(f"  ✓ Backup restored successfully")
            
            # Step 5: Cleanup temp postgres container
            DockerExecuter.stop_container(temp_pg_container, server_ip, self.user)
            DockerExecuter.remove_container(temp_pg_container, server_ip, self.user)
            log(f"  ✓ Cleanup complete")
            
            return True
            
        except Exception as e:
            log(f"Failed to restore Postgres: {e}")            
            log(traceback.format_exc())
            return False
    
    def _rollback_redis(
        self,        
        env: str,
        service: str,
        backup: Dict[str, Any],
        server_ip: str
    ) -> bool:
        """
        Restore Redis from backup RDB.
        
        Process:
        1. Upload backup RDB to server
        2. Stop redis container
        3. Copy RDB to data volume
        4. Start redis (will load from RDB)
        
        Args:           
            env: Environment name
            service: Service name (e.g., "redis")
            backup: Backup info dict with 'path' and 'filename'
            server_ip: Target server IP
            
        Returns:
            True if successful
        """
        try:            
            log(f"Restoring Redis backup on {server_ip}...")
            
            # Get container and volume names
            container_name = ResourceResolver.get_container_name(self.user, self.project_name, env, service)
            data_volume = ResourceResolver.get_docker_volume_name(self.user, self.project_name, env, "data", service)
            backups_volume = ResourceResolver.get_docker_volume_name(self.user, self.project_name, env, "backups", service)
            
            # Step 1: Upload backup to server's backup volume
            log(f"  Uploading backup file: {backup['filename']}")
            backup_file_path = backup['path']
            
            # Read backup file locally
            with open(backup_file_path, 'rb') as f:
                backup_data = f.read()
            
            # Write to server's backup volume using a temp container
            temp_upload_container = f"restore_upload_{int(time.time())}"
            upload_cmd = (
                f"docker run --rm -i --name {temp_upload_container} "
                f"-v {backups_volume}:/backups "
                f"alpine:latest sh -c 'cat > /backups/{backup['filename']}'"
            )
            
            CommandExecuter.run_cmd_with_stdin(
                upload_cmd, 
                backup_data, 
                server_ip, 
                self.user
            )
            log(f"  ✓ Backup uploaded to server")
            
            # Step 2: Stop redis container
            log(f"  Stopping {service} container...")
            try:
                DockerExecuter.stop_container(container_name, server_ip, self.user)
                log(f"  ✓ Container stopped")
            except Exception as e:
                log(f"  Warning: Could not stop container (may already be stopped): {e}")
            
            # Step 3: Copy RDB from backups volume to data volume
            log(f"  Copying backup to data volume...")
            
            temp_copy_container = f"restore_copy_{int(time.time())}"
            copy_cmd = (
                f"docker run --rm --name {temp_copy_container} "
                f"-v {backups_volume}:/backups:ro "
                f"-v {data_volume}:/data "
                f"alpine:latest "
                f"cp /backups/{backup['filename']} /data/dump.rdb"
            )
            
            CommandExecuter.run_cmd(copy_cmd, server_ip, self.user)
            log(f"  ✓ Backup copied to data volume")
            
            # Step 4: Set proper permissions on RDB file
            log(f"  Setting file permissions...")
            
            perms_cmd = (
                f"docker run --rm "
                f"-v {data_volume}:/data "
                f"alpine:latest "
                f"chmod 644 /data/dump.rdb"
            )
            
            CommandExecuter.run_cmd(perms_cmd, server_ip, self.user)
            log(f"  ✓ Permissions set")
            
            log(f"  ✓ Redis restore complete (will load on next start)")
            
            return True
            
        except Exception as e:
            log(f"Failed to restore Redis: {e}")        
            log(traceback.format_exc())
            return False

    def _get_credential(self, credentials: dict, key: str, env_key: str = None, required: bool = False) -> str:
        """
        Get credential with fallback to .env defaults.
        
        Priority:
        1. credentials dict (client provided)
        2. .env (your defaults)
        3. None (not available)
        
        Args:
            credentials: Credentials dictionary (can be None)
            key: Key in credentials dict
            env_key: Environment variable name (defaults to key.upper())
            required: If True, log warning when missing
        
        Returns:
            Credential value or None
            
        Example:
            token = self._get_credential(credentials, 'digitalocean_token', 'DIGITALOCEAN_API_TOKEN')
        """
        # Use provided credential
        if credentials and key in credentials and credentials[key]:
            return credentials[key]
        
        # Fallback to .env
        env_value = os.getenv(env_key or key.upper())
        if env_value:
            return env_value
        
        # Not found
        if required:
            log(f"⚠️  Credential '{key}' not provided and not in .env")
        
        return None

    def _get_registry_credentials(self, credentials: dict = None) -> tuple:
        """
        Get Docker registry credentials with fallback to .env defaults.
        
        Args:
            credentials: Credentials dictionary
        
        Returns:
            Tuple of (url, username, password) or (None, None, None)
            
        Example:
            url, user, pwd = self._get_registry_credentials(credentials)
        """
        username = self._get_credential(credentials, 'registry_username', 'DOCKER_REGISTRY_USERNAME')
        password = self._get_credential(credentials, 'registry_password', 'DOCKER_REGISTRY_PASSWORD')
        
        # Only return credentials if we have both username and password
        if username and password:
            url = self._get_credential(credentials, 'registry_url', 'DOCKER_REGISTRY_URL') or 'docker.io'
            log(f"Using registry credentials for {url}")
            return (url, username, password)
        
        # No credentials available
        log("⚠️  No registry credentials - assuming public images or manual docker login")
        return (None, None, None)

    def _get_git_token(self, service_config: dict, credentials: dict = None) -> str:
        """
        Get git token with fallback chain.
        
        Priority:
        1. Service-specific token (service_config['git_token'])
        2. Global token from credentials dict
        3. .env default (GIT_TOKEN)
        4. None (public repo)
        
        Args:
            service_config: Service configuration dict
            credentials: Credentials dictionary
        
        Returns:
            Git token or None
        """
        # Service-specific override
        if service_config.get('git_token'):
            return service_config['git_token']
        
        # Global from credentials
        if credentials and credentials.get('git_token'):
            return credentials['git_token']
        
        # .env default
        return os.getenv('GIT_TOKEN')

    def _login_all_servers(self, env: str, registry_url: str, 
                        username: str, password: str):
        """
        Login to Docker registry on all deployment servers in parallel.
        
        Args:
            env: Environment name
            registry_url: Registry URL
            username: Registry username
            password: Registry password
        """
        servers = self._get_deployment_servers(env)
        
        if not servers:
            return
        
        log(f"Logging into {registry_url} on {len(servers)} server(s)...")
        
        # Login to all servers in parallel
        from concurrent.futures import ThreadPoolExecutor
        
        def login_server(server_ip):
            try:
                return DockerExecuter.docker_login(
                    username=username,
                    password=password,
                    registry=registry_url,
                    server_ip=server_ip
                )
            except Exception as e:
                log(f"⚠️  Failed to login on {server_ip}: {e}")
                return False
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(login_server, ip): ip for ip in servers}
            
            for future in futures:
                future.result()  # Wait for all logins

    def _logout_all_servers(self, env: str, registry_url: str):
        """
        Logout from Docker registry on all servers (security cleanup).
        
        Args:
            env: Environment name
            registry_url: Registry URL
        """
        servers = self._get_deployment_servers(env)
        
        if not servers:
            return
        
        log(f"Logging out from {registry_url} on {len(servers)} server(s)...")
        
        # Logout from all servers in parallel
        from concurrent.futures import ThreadPoolExecutor
        
        def logout_server(server_ip):
            try:
                DockerExecuter.docker_logout(
                    registry=registry_url,
                    server_ip=server_ip
                )
            except Exception as e:
                log(f"⚠️  Failed to logout on {server_ip}: {e}")
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(logout_server, ip) for ip in servers]
            
            for future in futures:
                future.result()  # Wait for all logouts

    def _get_deployment_servers(self, env: str) -> List[str]:
        """
        Get all server IPs for this deployment.
        
        Args:
            env: Environment name
        
        Returns:
            List of server IP addresses
        """
        services = self.deployment_configurer.get_services(env)
        servers = set()
        
        for service_name, service_config in services.items():
            zone = service_config.get("server_zone", "lon1")
            if zone != "localhost":
                # Get servers for this zone from inventory
                try:
                    # Get all active servers in this zone
                    server_list = ServerInventory.get_servers(
                        deployment_status=ServerInventory.STATUS_ACTIVE,
                        zone=zone
                    )
                    servers.update([s['ip'] for s in server_list])
                except:
                    pass
        
        return list(servers)


