import os
import time
import requests
from typing import Dict, Any, List, Optional
from uuid import uuid4
from pathlib import Path
import constants
from deployment_config import DeploymentConfigurer
from deployment_naming import DeploymentNaming
from deployment_port_resolver import DeploymentPortResolver
from deployment_syncer import DeploymentSyncer
from execute_docker import DockerExecuter
from scheduler_manager import EnhancedCronManager
from cron_manager import CronManager
from logger import Logger
from server_inventory import ServerInventory
import env_loader


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
        auto_sync (bool): Whether to automatically sync during deployment operations.
    """

    def __init__(self, project_name: str, auto_sync: bool = True):
        """
        Initialize a Deployer instance for a specific project.

        Args:
            project_name: Name of the project to deploy
            auto_sync: Whether to automatically push config before and pull data after deployments.
            
        Raises:
            ValueError: If project_name not specified
            FileNotFoundError: If project not found
        """
        if not project_name:
            projects = DeploymentConfigurer.list_projects()
            if projects:
                raise ValueError(
                    f"Must specify project_name. Available projects: {', '.join(projects)}"
                )
            else:
                raise ValueError("No projects found in config/projects/")
        
        self.id = f'deployment_{uuid4()}'
        self.project_name = project_name
        self.deployment_configurer = DeploymentConfigurer(project_name)
        self.auto_sync = auto_sync
        
        # Save debug configs
        import json
        debug_path = constants.get_deployment_files_path(self.id)
        
        with open(debug_path / 'raw_config.json', 'w') as f:
            json.dump(self.deployment_configurer.raw_config, f, indent=4)
        
        with open(debug_path / 'project_info.txt', 'w') as f:
            f.write(f"Project: {self.project_name}\n")
            f.write(f"Config File: {self.deployment_configurer.config_file}\n")
            f.write(f"Deployment ID: {self.id}\n")
        
        # Save final processed config for audit/debug
        self.deployment_configurer.save_final_config(self.id)
        
        log(f"Initialized Deployer for project: {self.project_name}")

    def _get_version(self) -> str:
        """Get version - either override or from config"""
        if hasattr(self, '_override_version') and self._override_version:
            return self._override_version
        return self._get_version()

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
        project_name = self.deployment_configurer.get_project_name()
        
        if env:
            return DeploymentSyncer.push(project_name, env, targets)
        else:
            # Push to all environments
            success = True
            for environment in self.deployment_configurer.get_environments():
                if not DeploymentSyncer.push(project_name, environment, targets):
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
        project_name = self.deployment_configurer.get_project_name()
        
        if env:
            return DeploymentSyncer.pull(project_name, env, targets)
        else:
            # Pull from all environments
            success = True
            for environment in self.deployment_configurer.get_environments():
                if not DeploymentSyncer.pull(project_name, environment, targets):
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
        project_name = self.deployment_configurer.get_project_name()
        
        if env:
            return DeploymentSyncer.sync(project_name, env, targets)
        else:
            # Sync all environments
            success = True
            for environment in self.deployment_configurer.get_environments():
                if not DeploymentSyncer.sync(project_name, environment, targets):
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
        
        # Get /app directories from service volumes
        volumes = DeploymentSyncer.generate_service_volumes("temp", "temp", service_name)
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
        dockerfile_path = str(service_config.get("dockerfile", constants.get_dockerfiles_path() / Path(f"Dockerfile.{service_name}")))
        if os.path.exists(dockerfile_path):
            return dockerfile_path
        return None

    def build_images(self, environment: str = None, push_to_registry: bool = False):
        """
        Build Docker images for all enabled services.

        Logic:
        1. Check Docker availability.
        2. Iterate over environments (or a specific one if `environment` is provided).
        3. Iterate over all services in the environment:
           - Skip if disabled or `skip_build` is True.
           - Generate or locate Dockerfile.
           - Build Docker image with tag based on DeploymentNaming.
           - Optionally push the image to a registry.

        Args:
            environment (str, optional): Environment to build images for. Defaults to all.
            push_to_registry (bool, optional): Whether to push built images to Docker registry. Defaults to False.

        Returns:
            bool: True if build process completed successfully.

        Example:
            build_images(environment="dev", push_to_registry=True)
        """
        if not DockerExecuter.check_docker():
            log("Docker is not available. Please ensure Docker is installed and running.")
            return False

        log(f"Building images (push={push_to_registry})...")
        Logger.start()

        for env in self.deployment_configurer.get_environments():
            if environment is None or environment == env:
                for service_name, service_config in self.deployment_configurer.get_services(env).items():
                    log(f"Service: {service_name}...")
                    Logger.start()

                    if service_config.get("disabled", False):
                        log(f"Skipping disabled service: {service_name}")
                        Logger.end()
                        continue

                    if service_config.get("skip_build", False):
                        log(f"Skipping build for service (skip_build=True): {service_name}")
                        Logger.end()
                        continue

                    # Skip if no dockerfile specified (prebuilt image)
                    if not service_config.get("dockerfile") and not service_config.get("dockerfile_content"):
                        log(f"No dockerfile specified for {service_name}, skipping build (using prebuilt image)")
                        Logger.end()
                        continue

                    # Handle dockerfile_content vs dockerfile
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
                        log(f"Error: Could not create/find Dockerfile for {service_name}, skipping.")
                        Logger.end()
                        continue

                    docker_hub_user = self.deployment_configurer.get_docker_hub_user()
                    version = self._get_version()
                    project_name = self.deployment_configurer.get_project_name()

                    tag = DeploymentNaming.get_image_name(
                        docker_hub_user,
                        project_name,
                        env,
                        service_name,
                        version,
                        service_config.get("is_proxy", False)
                    )

                    build_context = service_config.get("build_context", ".")
                    log(f"Building image {tag} from {dockerfile}...")
                    
                    DockerExecuter.build_image(
                        dockerfile_path=dockerfile,
                        tag=tag,
                        context_dir=build_context,
                        progress="plain"
                    )

                    if push_to_registry:
                        log(f"Pushing {tag}...")
                        DockerExecuter.push_image(tag)
                    Logger.end()
                    log(f"{service_name} built.")

        Logger.end()
        log('Images built.')        
        return True

    def deploy(self, env: str = None, service_name: str = None, build: bool = True, target_version: str = None) -> bool:
        """
        Deploy services with immutable infrastructure.
        
        Args:
            env: Environment to deploy
            service_name: Specific service (None = all services)
            build: Whether to build images first
            target_version: Override version for rollback (None = use config version)
        
        Behavior:
        - deploy(env="dev")              → build dev + deploy dev
        - deploy(env="dev", build=False) → deploy dev only (no build)
        - deploy()                       → build all + deploy all
        - deploy(build=False)            → deploy all without build
        - deploy(env="dev", target_version="v1.2.3", build=False) → rollback
        
        Uses immutable infrastructure: new servers for each deployment,
        blue-green swap, automatic rollback on failure.
        """
        project_name = self.deployment_configurer.get_project_name()
        
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
            
            build_success = self.build_images(environment=env, push_to_registry=is_remote)
            Logger.end()
            
            if not build_success:
                log("Build failed - stopping deployment")
                return False
            
            log("Build complete")
        
        log(f'Deploying {project_name}, env: {env or "all"}, service: {service_name or "all"}')
        Logger.start()

        # Auto-sync: Push config before deployment
        if self.auto_sync:
            log("Auto-sync: Pushing config, secrets, and files...")
            environments = [env] if env else self.deployment_configurer.get_environments()
            for environment in environments:
                DeploymentSyncer.push(project_name, environment)

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

        # Deploy each service
        for svc_name, config in services.items():
            log(f'Deploying {svc_name}')
            Logger.start()
            
            if self.is_service_scheduled(config):
                # Scheduled services: use simple deployment (can't do blue-green on cron jobs)
                log(f"{svc_name} is scheduled - using simple deployment")
                
                # For scheduled services, still need a server
                # Use first available or create one
                zone = config.get("server_zone", "lon1")
                cpu = config.get("server_cpu", 1)
                memory = config.get("server_memory", 1024)
                
                servers = ServerInventory.get_servers(
                    deployment_status=ServerInventory.STATUS_GREEN,
                    zone=zone,
                    cpu=cpu,
                    memory=memory
                )
                
                if not servers:
                    # No green servers with matching specs, claim one
                    server_ips = ServerInventory.claim_servers(1, zone, cpu, memory)
                    ServerInventory.promote_blue_to_green(server_ips)
                    server = server_ips[0]
                else:
                    server = servers[0]['ip']
                
                # Remove old scheduled job
                EnhancedCronManager.remove_scheduled_service(project_name, env or 'dev', svc_name, server)
                
                # Clean up old containers
                try:
                    CronManager.cleanup_old_containers(project_name, env or 'dev', svc_name, server)
                except Exception as e:
                    log(f"Warning: Could not cleanup containers: {e}")
                
                # Install new scheduled job
                self.install_scheduled_service(project_name, env or 'dev', svc_name, config, server)
            else:
                # Long-running services: use immutable infrastructure
                success = self._deploy_immutable(project_name, env or 'dev', svc_name, config)
                
                if not success:
                    log(f"Deployment failed for {svc_name}")
                    Logger.end()
                    return False
            
            Logger.end()
            log(f'{svc_name} deployed')

        # Nginx automation: Setup SSL/DNS for services with domains
        log("Checking for nginx automation...")
        self._setup_nginx_automation(env or 'dev', services)

        # Auto-sync: Pull data after deployment
        if self.auto_sync:
            log("Auto-sync: Pulling data and logs...")
            environments = [env] if env else self.deployment_configurer.get_environments()
            for environment in environments:
                time.sleep(2)
                DeploymentSyncer.pull(project_name, environment)

        Logger.end()
        log('Deployment complete')
        return True

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
                    from nginx_config_generator import NginxConfigGenerator
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
                    from nginx_config_generator import NginxConfigGenerator
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
                    from nginx_config_generator import NginxConfigGenerator
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
    # EXISTING DEPLOYMENT METHODS
    # =============================================================================

    def _deploy_immutable(
        self,
        project: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any]
    ) -> bool:
        """
        Deploy with immutable infrastructure strategy.
        
        Process:
        1. Ensure required servers exist (claim from pool or create new)
        2. Deploy to blue servers
        3. Health check all blues
        4. If healthy: promote blue→green, old green→reserve
        5. If failed: release blues back to reserve

        Notes:
        You need keep_reserve set to True in the config to keep a reserve of servers. Otherwise unused server sare destroyed.
        
        Returns:
            True if deployment successful
        """
        log(f"Immutable deployment for {service_name}")
        Logger.start()
        
        # Get server requirements from config with defaults
        servers_count = service_config.get("servers_count", 1)
        zone = service_config.get("server_zone", "lon1")
        cpu = service_config.get("server_cpu", 1)
        memory = service_config.get("server_memory", 1024)
        
        # Claim servers (creates if needed, marks as blue)
        try:
            blue_ips = ServerInventory.claim_servers(
                count=servers_count,
                zone=zone,
                cpu=cpu,
                memory=memory
            )
        except Exception as e:
            log(f"Failed to claim servers: {e}")
            Logger.end()
            return False
        
        # Deploy to all blue servers
        deployment_success = True
        for blue_ip in blue_ips:
            log(f"Deploying to blue server {blue_ip}")
            Logger.start()
            
            try:
                # Create network
                self.create_containers_network(env, blue_ip)
                
                # Start service on this blue server
                self.start_service(project, env, service_name, service_config, blue_ip)
                
            except Exception as e:
                log(f"Deployment failed on {blue_ip}: {e}")
                deployment_success = False
                break
            
            Logger.end()
        
        # Health check all blue servers
        if deployment_success:
            log("Health checking blue servers...")
            for blue_ip in blue_ips:
                # Get port for health check
                dockerfile = service_config.get("dockerfile")
                container_ports = DeploymentPortResolver.get_container_ports(service_name, dockerfile)
                
                if container_ports:
                    port = DeploymentPortResolver.generate_host_port(project, env, service_name, container_ports[0])
                    url = f"http://{blue_ip}:{port}"
                    
                    if not self.wait_for_health_check(url, timeout=30, service_name=service_name):
                        log(f"Health check failed on {blue_ip}")
                        deployment_success = False
                        break
        
        destroy_old = service_config.get("keep_reserve", False)

        # Swap or rollback
        if deployment_success:
            log("All blues healthy - promoting to green")
            old_green_ips = ServerInventory.promote_blue_to_green(blue_ips, project, env)            

            from deployment_state_manager import DeploymentStateManager
            DeploymentStateManager.record_deployment(
                project=project,
                env=env,
                service=service_name,
                servers=blue_ips,
                container_name=DeploymentNaming.get_container_name(project, env, service_name),
                version=self._get_version()
            )

            # Update nginx if this service has a domain
            if service_config.get("domain"):
                self._update_nginx_for_new_servers(project, env, service_name, service_config, blue_ips)
            
            # Optionally destroy old greens (or keep in reserve pool)            
            if old_green_ips:
                ServerInventory.release_servers(old_green_ips, destroy=destroy_old)
            
            Logger.end()
            log(f"Immutable deployment successful")
            return True
        else:
            log("Deployment failed - rolling back")
            
            # Stop containers on blue servers
            for blue_ip in blue_ips:
                try:
                    container_name = DeploymentNaming.get_container_name(project, env, service_name)
                    DockerExecuter.stop_and_remove_container(container_name, blue_ip, ignore_if_not_exists=True)
                except Exception as e:
                    log(f"Cleanup failed on {blue_ip}: {e}")
            
            # Release blues back to reserve
            ServerInventory.release_servers(blue_ips, destroy=destroy_old)
            
            Logger.end()
            log("Rollback complete - old green servers still running")
            return False

    def _update_nginx_for_new_servers(
        self,
        project: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        new_server_ips: List[str]
    ):
        """Update nginx configuration to point to new server IPs"""
        log(f"Updating nginx configuration for new servers: {new_server_ips}")
        
        # Nginx should be on first server
        nginx_server = new_server_ips[0]
        
        # Re-run nginx setup with new backend IPs
        cf_token = os.getenv("CLOUDFLARE_API_TOKEN")
        email = os.getenv("CLOUDFLARE_EMAIL")
        admin_ip = os.getenv("ADMIN_IP")
        
        if admin_ip and admin_ip.lower() == "auto":
            admin_ip = None
        
        try:
            from nginx_config_generator import NginxConfigGenerator
            
            # Create updated config with actual server IPs for nginx upstream
            updated_config = {
                **service_config,
                'servers': new_server_ips  # Override with actual IPs for nginx
            }
            
            NginxConfigGenerator.setup_service(
                project=project,
                env=env,
                service_name=service_name,
                service_config=updated_config,
                target_server=nginx_server,
                email=email,
                cloudflare_api_token=cf_token,
                admin_ip=admin_ip,
                auto_firewall=True
            )
            log("Nginx configuration updated successfully")
        except Exception as e:
            log(f"Warning: Nginx update failed: {e}")

    def create_containers_network(self, env: str = None, server: str = 'localhost'):
        """Create Docker network for containers"""
        project_name = self.deployment_configurer.get_project_name()
        network_name = DeploymentNaming.get_network_name(project_name, env)
        
        if DockerExecuter.network_exists(network_name, server):
            log(f"Network {network_name} already exists on {server}")
        else:
            DockerExecuter.create_network(network_name, server, ignore_if_exists=True)
            log(f"Network {network_name} created on {server}")

    def get_services_by_startup_order(self, env: str = None) -> Dict[str, Any]:
        """Get services sorted by startup order, separating scheduled from long-running services"""
        
        services_with_order = []
        envs = [env] if env else self.deployment_configurer.get_environments()

        for e in envs:
            for name, config in self.deployment_configurer.get_services(e).items():
                if not config.get("disabled", False):
                    # Scheduled services get higher priority (lower order) to ensure images are built first
                    order = config.get("startup_order", 999)
                    services_with_order.append((order, name, config))

        sorted_services = sorted(services_with_order, key=lambda x: x[0])
        return {name: cfg for _, name, cfg in sorted_services}
   
    def wait_for_health_check(self, url: str, timeout: int = 30, interval: int = 5, service_name: str=None) -> bool:
        """Wait for service to respond to HTTP requests"""  
        if service_name in ['redis', 'postgres']:
            log(f"Skipping HTTP health check for {service_name} (non-HTTP service)")
            return True      
        for i in range(0, timeout, interval):
            try:
                response = requests.get(url, timeout=3)
                if 200 <= response.status_code < 300:  # Any 2xx response                   
                    return True
            except requests.exceptions.RequestException:              
                pass            
            if i % (interval * 4) == 0 and i > 0:  # Show progress every 20 seconds
                log(f"  Still waiting for {url}... (timeout in {timeout-i} seconds)")                
            time.sleep(interval)        
        return False

    def _create_required_volumes(self, project_name: str, env: str, server_ip: str = 'localhost'):
        """Create all required Docker volumes for the project/environment"""
        volumes = DeploymentSyncer.generate_docker_volumes(project_name, env)
        
        for volume_name in volumes.keys():
            # Check if volume exists first
            if not DockerExecuter.volume_exists(volume_name, server_ip):
                DockerExecuter.create_volume(volume_name, server_ip)
                log(f"Created Docker volume: {volume_name}")
            else:
                log(f"Docker volume already exists: {volume_name}")

    def start_service(
        self,
        project_name: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        server_ip: str = None,
        user: str = "root"
    ) -> bool:
        """Start a single service - either as long-running container or scheduled cron job."""
        log(f"Starting {service_name}...") 

        # Check if this is a scheduled service
        if self.is_service_scheduled(service_config):
            return self.install_scheduled_service(
                project_name, env, service_name, service_config, server_ip, user
            )
        else:
            return self.start_long_running_service(
                project_name, env, service_name, service_config, server_ip, user
            ) 

    def start_long_running_service(
        self,
        project_name: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        server_ip: str = None,
        user: str = "root"
    ) -> bool:
        """Start a long-running service container."""
        container_name = DeploymentNaming.get_container_name(project_name, env, service_name)        
        network_name = DeploymentNaming.get_network_name(project_name, env)

        # Create required Docker volumes first
        self._create_required_volumes(project_name, env, server_ip or 'localhost')

        # Get port mappings (auto-detected)
        dockerfile = service_config.get("dockerfile")
        container_ports = DeploymentPortResolver.get_container_ports(service_name, dockerfile)
        ports = {}
        host_ports = []
        
        for container_port in container_ports:
            host_port = DeploymentPortResolver.generate_host_port(
                project_name, env, service_name, container_port
            )
            host_ports.append(host_port)
            ports[str(host_port)] = str(container_port)

        # Use auto-generated volumes from DeploymentSyncer
        volumes = DeploymentSyncer.generate_service_volumes(
            project_name, env, service_name, use_docker_volumes=True
        )

        # Get image name - use provided image or generate build name
        if service_config.get("image"):
            # Use specified image (for prebuilt services like postgres)
            image = service_config["image"]
        else:
            # Generate image name for built services
            docker_hub_user = self.deployment_configurer.get_docker_hub_user()
            version = self._get_version()
            image = DeploymentNaming.get_image_name(
                docker_hub_user,
                project_name,
                env,
                service_name,
                version,
                service_config.get("is_proxy", False)
            )

        if server_ip and server_ip != 'localhost':
            log(f"Pulling image {image} to {server_ip}...")
            from execute_docker import DockerExecuter
            DockerExecuter.pull_image(image, server_ip, user)

        log(f"Using volumes for {service_name}: {volumes}")

        # Get restart policy from service config (default to unless-stopped for backward compatibility)
        restart_policy = "unless-stopped" if service_config.get("restart", True) else "no"

        # Run container using DockerExecuter
        DockerExecuter.run_container(
            image=image,
            name=container_name,
            network=service_config.get("network_name", network_name),
            ports=ports,
            volumes=volumes,
            environment=service_config.get("env_vars", {}),
            restart_policy=restart_policy,
            server_ip=server_ip,
            user=user
        )

        # Wait for health check for long-running services only
        timeout = 30
        if host_ports:
            port = host_ports[0]
            host = server_ip or "localhost"
            url = f"http://{host}:{port}"
            if self.wait_for_health_check(url, timeout, service_name=service_name):
                from deployment_state_manager import DeploymentStateManager
                DeploymentStateManager.record_deployment(
                    project=project_name,
                    env=env,
                    service=service_name,
                    servers=[server_ip or "localhost"],
                    container_name=container_name,
                    version=self._get_version()
                )
                return True
            else:
                log(f"Warning: {service_name} did not respond within {timeout} seconds")
                log(f"  Service may still be starting up. Check manually at {url}")
                return False        
        else:
            log(f"No ports detected for {service_name}, skipping health check")
            return True

    def install_scheduled_service(
        self,
        project_name: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        server_ip: str = None,
        user: str = "root"
    ) -> bool:
        """Install a scheduled service with cross-platform support."""
        log(f"Installing scheduled service {service_name} (schedule: {service_config.get('schedule')})")

        # Validate schedule
        schedule = service_config.get("schedule")
        if not CronManager.validate_cron_schedule(schedule):
            log(f"Error: Invalid cron schedule '{schedule}' for service {service_name}")
            return False

        # Create network (needed for scheduled containers)
        self.create_containers_network(env, server_ip or 'localhost')

        # Create required Docker volumes
        self._create_required_volumes(project_name, env, server_ip or 'localhost')

        # Install scheduled service with platform detection
        docker_hub_user = self.deployment_configurer.get_docker_hub_user()
        version = self._get_version()

        success = EnhancedCronManager.install_scheduled_service(
            project_name, env, service_name, service_config,
            docker_hub_user, version, server_ip or 'localhost', user
        )

        if success:
            log(f"Scheduled service {service_name} installed successfully")
        else:
            log(f"Failed to install scheduled service {service_name}")

        return success

    def create_temporary_dockerfile(self, dockerfile_content: Dict[str, str], service_name: str) -> str:
        """Create a temporary Dockerfile from content and inject app directories"""
        
        # Generate base Dockerfile content
        base_content = self.generate_dockerfile_from_content(dockerfile_content)
        if not base_content:
            return None
        
        # Get /app directories from service volumes
        volumes = DeploymentSyncer.generate_service_volumes("temp", "temp", service_name)
        app_dirs = set()
        
        for volume in volumes:
            if ':' in volume:
                container_path = volume.split(':', 1)[1].split(':')[0]
                if container_path.startswith('/app/'):
                    app_dirs.add(container_path)
        
        # If no /app directories, return content as-is
        if not app_dirs:
            return base_content
        
        # Inject app directories
        lines = base_content.split('\n')
        
        # Find insertion point (after WORKDIR /app or before CMD/ENTRYPOINT)
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
        
        # Build directory creation command
        sorted_dirs = sorted(app_dirs)
        app_dirs_creation = [
            "",
            "# AUTO-GENERATED: Create required directories for volume mounts"
        ]
        
        if len(sorted_dirs) == 1:
            app_dirs_creation.append(f"RUN mkdir -p {sorted_dirs[0]}")
        else:
            mkdir_cmd = f"RUN mkdir -p {' '.join(sorted_dirs)}"
            app_dirs_creation.append(mkdir_cmd)
        
        app_dirs_creation.extend(["# END AUTO-GENERATED", ""])
        
        # Insert the directory creation
        for i, new_line in enumerate(app_dirs_creation):
            lines.insert(insert_index + i, new_line)
        
        return '\n'.join(lines)

    def generate_dockerfile_from_content(self, dockerfile_content: Dict[str, str]) -> str:
        """Generate Dockerfile content from numbered dictionary"""
        if not dockerfile_content:
            return None
        
        # Sort by numeric keys (handle both "1" and "1.1" style keys)
        def sort_key(key):
            parts = key.split('.')
            return [int(part) for part in parts]
        
        sorted_keys = sorted(dockerfile_content.keys(), key=sort_key)
        
        # Generate Dockerfile lines
        dockerfile_lines = []
        for key in sorted_keys:
            dockerfile_lines.append(dockerfile_content[key])
        
        return '\n'.join(dockerfile_lines)

    def write_temporary_dockerfile(self, dockerfile_content: str, service_name: str, env: str) -> str:
        """Write dockerfile content to a temporary file"""

        temp_dir = constants.get_deployment_files_path(self.id)

        temp_dockerfile = temp_dir / f"Dockerfile.{service_name}.{env}"
        
        with open(temp_dockerfile, 'w', encoding='utf-8') as f:
            f.write(dockerfile_content)
        
        log(f"Created temporary Dockerfile: {temp_dockerfile}")
        return str(temp_dockerfile)
    

    def list_deployments(self, env: str = None, include_costs: bool = True) -> Dict[str, Any]:
        """
        Show current deployment state across all services and servers.
        
        Args:
            env: Filter by environment (None = all environments)
            include_costs: Include DigitalOcean cost information
            
        Returns:
            Dictionary with deployment status
        """
        from deployment_state_manager import DeploymentStateManager
        from server_inventory import ServerInventory
        from do_cost_tracker import DOCostTracker
        
        log("Gathering deployment status...")
        Logger.start()
        
        # Sync server inventory first
        ServerInventory.sync_with_digitalocean()
        
        result = {
            'project': self.project_name,
            'environments': {},
            'costs': None
        }
        
        # Get cost information
        if include_costs:
            try:
                result['costs'] = DOCostTracker.get_cost_breakdown()
            except Exception as e:
                log(f"Warning: Could not fetch cost information: {e}")
                result['costs'] = None
        
        # Get all deployed services
        environments = [env] if env else self.deployment_configurer.get_environments()
        
        for environment in environments:
            env_services = DeploymentStateManager.get_all_services(
                project=self.project_name,
                env=environment
            )
            
            if not env_services:
                continue
            
            result['environments'][environment] = {
                'services': {},
                'servers': {}
            }
            
            # Process each service
            for service_name, deployment_info in env_services.items():
                if not deployment_info:
                    continue
                
                result['environments'][environment]['services'][service_name] = {
                    'version': deployment_info.get('version', 'unknown'),
                    'servers': deployment_info.get('servers', []),
                    'container_name': deployment_info.get('container_name'),
                    'deployed_at': deployment_info.get('deployed_at'),
                    'status': 'running'  # Could enhance with actual health check
                }
                
                # Track which services run on which servers
                for server_ip in deployment_info.get('servers', []):
                    if server_ip not in result['environments'][environment]['servers']:
                        # Get server info from inventory
                        server_info = ServerInventory.get_server_by_ip(server_ip)
                        
                        result['environments'][environment]['servers'][server_ip] = {
                            'zone': server_info.get('zone', 'unknown') if server_info else 'unknown',
                            'status': server_info.get('deployment_status', 'unknown') if server_info else 'unknown',
                            'cpu': server_info.get('cpu') if server_info else None,
                            'memory': server_info.get('memory') if server_info else None,
                            'services': []
                        }
                    
                    result['environments'][environment]['servers'][server_ip]['services'].append(service_name)
        
        Logger.end()
        log("Deployment status gathered")
        
        return result


    def print_deployments(self, env: str = None):
        """
        Pretty-print deployment status to console.
        
        Args:
            env: Filter by environment (None = all)
        """
        status = self.list_deployments(env)
        
        print(f"\nProject: {status['project']}")
        print("=" * 80)
        
        for env_name, env_data in status['environments'].items():
            print(f"\nEnvironment: {env_name}")
            print("-" * 80)
            
            # Services section
            print("\nServices:")
            if not env_data['services']:
                print("  No services deployed")
            else:
                for service_name, service_info in env_data['services'].items():
                    print(f"\n  {service_name}")
                    print(f"    Version: {service_info['version']}")
                    print(f"    Container: {service_info['container_name']}")
                    print(f"    Deployed: {service_info['deployed_at']}")
                    print(f"    Servers: {', '.join(service_info['servers'])}")
            
            # Servers section
            print("\nServers:")
            if not env_data['servers']:
                print("  No servers")
            else:
                for server_ip, server_info in env_data['servers'].items():
                    status_marker = {
                        'green': '✓',
                        'blue': '○',
                        'reserve': '□',
                        'destroying': '✗'
                    }.get(server_info['status'], '?')
                    
                    print(f"\n  {status_marker} {server_ip} ({server_info['zone']}) - {server_info['status']}")
                    if server_info.get('cpu') and server_info.get('memory'):
                        print(f"    Resources: {server_info['cpu']} CPU, {server_info['memory']}MB RAM")
                    print(f"    Services: {', '.join(server_info['services'])}")
            
            print()


    def logs(
        self,
        service_name: str,
        env: str = None,
        lines: int = 100,
        follow: bool = False
    ) -> str:
        """
        Fetch logs from service containers across all servers.
        
        Args:
            service_name: Service to get logs from
            env: Environment (defaults to first environment if not specified)
            lines: Number of lines to tail from each server
            follow: Stream logs (not implemented - use docker logs -f directly)
            
        Returns:
            Formatted log output
        """
        from deployment_state_manager import DeploymentStateManager
        from execute_docker import DockerExecuter
        
        # Determine environment
        if not env:
            environments = self.deployment_configurer.get_environments()
            if not environments:
                log("No environments configured")
                return ""
            env = environments[0]
            log(f"Using environment: {env}")
        
        log(f"Fetching logs for {service_name} in {env}...")
        Logger.start()
        
        # Get service deployment info
        deployment = DeploymentStateManager.get_current_deployment(
            self.project_name,
            env,
            service_name
        )
        
        if not deployment:
            log(f"Service {service_name} not found in {env}")
            Logger.end()
            return f"Service {service_name} not deployed in environment {env}"
        
        servers = deployment.get('servers', [])
        container_name = deployment.get('container_name')
        
        if not servers:
            log("No servers found for service")
            Logger.end()
            return f"No servers running {service_name}"
        
        log(f"Fetching logs from {len(servers)} server(s)...")
        
        # Aggregate logs from all servers
        all_logs = []
        
        for server_ip in servers:
            log(f"Fetching from {server_ip}...")
            
            try:
                server_logs = DockerExecuter.get_container_logs(
                    container_name,
                    lines=lines,
                    server_ip=server_ip,
                    user="root"
                )
                
                # Format with server prefix if multiple servers
                if len(servers) > 1:
                    header = f"\n{'='*60}\nServer: {server_ip}\n{'='*60}\n"
                    all_logs.append(header + server_logs)
                else:
                    all_logs.append(server_logs)
                    
            except Exception as e:
                error_msg = f"\nError fetching logs from {server_ip}: {e}\n"
                all_logs.append(error_msg)
                log(f"Error: {e}")
        
        Logger.end()
        log(f"Logs fetched from {len(servers)} server(s)")
        
        result = "\n".join(all_logs)
        
        if follow:
            log("Note: follow mode not supported in Python API. Use 'docker logs -f' directly on server.")
        
        return result


    def print_logs(
        self,
        service_name: str,
        env: str = None,
        lines: int = 100
    ):
        """
        Fetch and print logs to console.
        
        Args:
            service_name: Service to get logs from
            env: Environment
            lines: Number of lines to tail
        """
        logs = self.logs(service_name, env, lines)
        print(logs)

