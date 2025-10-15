import os
import time
import requests
from typing import Dict, Any, List, Optional
from uuid import uuid4
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict

import constants
from execute_cmd import CommandExecuter
from nginx_config_generator import NginxConfigGenerator
from deployment_config import DeploymentConfigurer
from deployment_naming import DeploymentNaming
from deployment_port_resolver import DeploymentPortResolver
from deployment_syncer import DeploymentSyncer
from execute_docker import DockerExecuter
from deployment_state_manager import DeploymentStateManager
from scheduler_manager import EnhancedCronManager
from cron_manager import CronManager
from logger import Logger
from server_inventory import ServerInventory
from do_cost_tracker import DOCostTracker
import env_loader
from path_resolver import PathResolver
from do_manager import DOManager

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
        
        # Use localhost for Dockerfile generation (build context)
        volumes = PathResolver.generate_all_volume_mounts(
            "temp", "temp", service_name,
            server_ip="localhost",  # Dockerfile build happens locally
            use_docker_volumes=True,
            user="root",
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
                        version
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
        Deploy services with immutable infrastructure and parallel execution.
        
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
        
        Uses immutable infrastructure with parallel deployment:
        - Services with same startup_order deploy in parallel
        - Each service deploys to multiple droplets in parallel
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

        self.pre_provision_servers(env, service_name)

        # Auto-sync: Push config before deployment
        if self.auto_sync:
            log("Auto-sync: Pushing config, secrets, and files...")
            environments = [env] if env else self.deployment_configurer.get_environments()
            
            for environment in environments:
                # Get all servers that will receive deployments
                all_servers = ServerInventory.list_all_servers()
                
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
                    DeploymentSyncer.push(project_name, environment, targets=target_ips)
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
                    
                    for server_ip in target_servers:
                        self.install_scheduled_service(project_name, env or 'dev', svc_name, config, server_ip)
                else:
                    success = self._deploy_immutable(project_name, env or 'dev', svc_name, config)
                    
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
                            project_name, env or 'dev', svc_name, config
                        )
                        service_futures[future] = svc_name
                    
                    for future in as_completed(service_futures):
                        svc_name = service_futures[future]
                        try:
                            result = future.result()
                            if result['success']:
                                log(f"✓ {svc_name} deployed successfully")
                            else:
                                log(f"✗ {svc_name} deployment failed: {result.get('error', 'Unknown error')}")
                                all_success = False
                        except Exception as e:
                            log(f"✗ {svc_name} deployment exception: {e}")
                            all_success = False
                
                if not all_success:
                    log(f"Deployment failed in startup_order {order} - aborting")
                    break
        
        if not all_success:
            Logger.end()
            return False

        # CLEANUP: Find servers with no services and manage them
        log("Performing server cleanup...")
        self._cleanup_empty_servers(project_name, env)

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
        project: str,
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
            project: Project name
            env: Environment
            service: Service name
            server_ip: Target server IP
            base_port: Base host port (e.g., 8357)
            base_name: Base container name
            
        Returns:
            {"port": 8357, "name": "base_name"} or
            {"port": 18357, "name": "base_name_secondary"}
        """
        existing = DockerExecuter.find_service_container(project, env, service, server_ip)
        
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
        project: str,
        env: str,
        service: str,
        deployed_servers: List[str]
    ) -> str:
        """
        Determine nginx backend mode FOR THIS SPECIFIC SERVICE.
        
        CRITICAL: This is per-service, not global!
        
        Decision:
        - Service on 1 server → "single_server" (use container names, no port mapping)
        - Service on 2+ servers → "multi_server" (use IPs + host ports)
        
        Args:
            project: Project name
            env: Environment
            service: Service name
            deployed_servers: Servers where THIS SERVICE is deployed
            
        Returns:
            "single_server" or "multi_server"
        """
        if len(deployed_servers) == 1:
            log(f"Backend mode for {service}: single_server (deployed on {deployed_servers[0]} only)")
            return "single_server"
        else:
            log(f"Backend mode for {service}: multi_server (deployed on {len(deployed_servers)} servers)")
            return "multi_server"

    def _generate_nginx_backends(
        self,
        mode: str,
        project: str,
        env: str,
        service: str,
        deployed_servers: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Generate backend list for nginx based on deployment mode.
        
        Args:
            mode: "single_server" or "multi_server"
            project: Project name
            env: Environment
            service: Service name
            deployed_servers: Servers where THIS SERVICE is deployed
            
        Returns:
            Single-server: [{"container_name": "...", "port": "5432"}, ...]
            Multi-server: [{"ip": "...", "port": 8357}, ...]
        """
        if mode == "single_server":
            # Use container names (Docker DNS)
            backends = []
            server_ip = deployed_servers[0]
            
            # Get both primary and secondary containers if they exist
            for suffix in ["", "_secondary"]:
                container_name = DeploymentNaming.get_container_name(project, env, service)
                if suffix:
                    container_name = f"{container_name}{suffix}"
                
                # Check if container exists
                if DockerExecuter.container_exists(container_name, server_ip):
                    # Get container port (not host port!)
                    service_config = self.deployment_configurer.get_services(env)[service]
                    dockerfile = service_config.get("dockerfile")
                    container_ports = DeploymentPortResolver.get_container_ports(service, dockerfile)
                    container_port = container_ports[0] if container_ports else "8000"
                    
                    backends.append({
                        "container_name": container_name,
                        "port": container_port  # Container port (5432, not 8357)
                    })
            
            return backends
        
        else:  # multi_server
            # Use IP + host ports
            backends = []
            
            for server_ip in deployed_servers:
                # Find what's actually running on this server
                base_name = DeploymentNaming.get_container_name(project, env, service)
                
                # Check for both primary and secondary
                for suffix in ["", "_secondary"]:
                    container_name = f"{base_name}{suffix}" if suffix else base_name
                    
                    if DockerExecuter.container_exists(container_name, server_ip):
                        # Get published host port
                        port_map = DockerExecuter.get_published_ports(container_name, server_ip)
                        
                        # Find the host port (format: "5432/tcp" -> "8357")
                        service_config = self.deployment_configurer.get_services(env)[service]
                        dockerfile = service_config.get("dockerfile")
                        container_ports = DeploymentPortResolver.get_container_ports(service, dockerfile)
                        container_port = container_ports[0] if container_ports else "8000"
                        
                        port_key = f"{container_port}/tcp"
                        if port_key in port_map:
                            host_port = port_map[port_key]
                            
                            backends.append({
                                "ip": server_ip,
                                "port": host_port  # Host port (8357 or 18357)
                            })
            
            return backends

    def _update_all_nginx_for_service(
        self,
        project: str,
        env: str,
        service: str,
        deployed_servers: List[str],
        all_zone_servers: List[Dict[str, Any]]
    ) -> None:
        """
        Update nginx stream config on all servers in zone FOR THIS SERVICE.
        
        CRITICAL: Mode is determined per-service, not globally!
        
        This allows mixed deployments where some services are single-server
        and others are multi-server within the same zone.
        
        Args:
            project: Project name
            env: Environment
            service: Service name
            deployed_servers: Servers where THIS SERVICE is deployed
            all_zone_servers: All servers in the zone (for nginx updates)
        """
        if not self._is_tcp_service(service):
            log(f"Skipping nginx stream config for {service} (not a TCP service)")
            return
        
        # Determine mode FOR THIS SPECIFIC SERVICE
        mode = self._determine_backend_mode_for_service(
            project, env, service, deployed_servers
        )
        
        log(f"Updating nginx stream config for {service} (mode: {mode})")
        
        # Generate backends based on THIS SERVICE's deployment
        backends = self._generate_nginx_backends(
            mode, project, env, service, deployed_servers
        )
        
        if not backends:
            log(f"No backends found for {service}")
            return
        
        # Calculate internal port (stable for this service)
        internal_port = DeploymentPortResolver.get_internal_port(project, env, service)
        log(f"Internal port for {service}: {internal_port}")
        
        # Update nginx on EVERY server in the zone
        # (Even servers that don't run this service need the config)
        for server in all_zone_servers:
            try:
                NginxConfigGenerator.update_stream_config_on_server(
                    server['ip'], project, env, service, backends, internal_port, mode
                )
            except Exception as e:
                log(f"Warning: Failed to update nginx on {server['ip']}: {e}")

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
        Get all green servers in a zone.
        
        Args:
            zone: Zone name
            
        Returns:
            List of server dicts
        """
        return ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_ACTIVE,
            zone=zone
        )

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
        project_name: str,
        env: str,
        svc_name: str,
        config: Dict[str, Any]
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
                
                for server_ip in target_servers:
                    self.install_scheduled_service(project_name, env, svc_name, config, server_ip)
                
                result['success'] = True
                
            else:
                success = self._deploy_immutable(project_name, env, svc_name, config)
                
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
            project: str,
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
            
            Returns:
                Dict with:
                - 'success' (bool): Whether deployment succeeded
                - 'error' (str, optional): Error message if failed
                - 'container_name' (str, optional): Name of deployed container (for rollback)
            """
            result = {'success': False, 'error': None, 'container_name': None}
            
            try:
                log(f"[{target_ip}] Starting deployment")
                
                # Ensure network exists
                self.create_containers_network(env, target_ip)
                log(f"[{target_ip}] Network ready")
                
                # Determine toggle (base vs secondary)
                toggle = self._determine_toggle(project, env, service_name, target_ip, base_port, base_name)
                new_name = toggle["name"]
                new_port = toggle["port"]
                
                if new_name == base_name:
                    log(f"[{target_ip}] First deployment of {service_name} - using base")
                else:
                    log(f"[{target_ip}] Toggle deployment - using {'base' if new_port == base_port else 'secondary'}")
                
                log(f"[{target_ip}] Using container name: {new_name}, port: {new_port}")
                
                # Store container name for rollback tracking
                result['container_name'] = new_name
                
                # Create directories for volumes
                PathResolver.ensure_host_directories(project, env, service_name, target_ip, "root")
                log(f"[{target_ip}] Directories ready")
                
                # Create Docker volumes
                PathResolver.ensure_docker_volumes(project, env, service_name, target_ip, "root")
                log(f"[{target_ip}] Volumes ready")
                
                # Pull image if remote
                if service_config.get("image"):
                    image = service_config["image"]
                else:
                    docker_hub_user = self.deployment_configurer.get_docker_hub_user()
                    version = self._get_version()
                    image = DeploymentNaming.get_image_name(
                        docker_hub_user, project, env, service_name, version
                    )
                
                if target_ip != 'localhost':
                    log(f"[{target_ip}] Pulling image {image}...")
                    DockerExecuter.pull_image(image, target_ip, "root")
                
                # Start new container
                volumes = PathResolver.generate_all_volume_mounts(
                    project, env, service_name, target_ip,
                    use_docker_volumes=True, user="root", auto_create_dirs=False
                )
                
                network_name = DeploymentNaming.get_network_name(project, env)
                env_vars = service_config.get("env_vars", {})
                restart_policy = "unless-stopped" if service_config.get("restart", True) else "no"
                
                # Port mapping only if multi-server
                dockerfile = service_config.get("dockerfile")
                container_ports = DeploymentPortResolver.get_container_ports(service_name, dockerfile)
                container_port = container_ports[0] if container_ports else "8000"
                
                ports = None
                if need_port_mapping:
                    ports = {str(new_port): str(container_port)}
                
                DockerExecuter.run_container(
                    image=image,
                    name=new_name,
                    network=network_name,
                    ports=ports,
                    volumes=volumes,
                    environment=env_vars,
                    restart_policy=restart_policy,
                    server_ip=target_ip,
                    user="root"
                )
                
                log(f"[{target_ip}] Started {new_name}")
                
                # Health check
                health_check_passed = self._verify_container_health(
                    service_name, service_config, new_name, target_ip
                )
                
                if not health_check_passed:
                    log(f"[{target_ip}] Health check failed - rolling back")
                    DockerExecuter.stop_and_remove_container(new_name, target_ip, ignore_if_not_exists=True)
                    result['error'] = 'Health check failed'
                    return result
                
                log(f"[{target_ip}] Health check passed")
                
                # Stop old container
                old_name = self._get_opposite_container_name(new_name, base_name)
                if old_name:
                    DockerExecuter.stop_and_remove_container(
                        old_name, target_ip, ignore_if_not_exists=True
                    )
                    log(f"[{target_ip}] Stopped old container {old_name}")
                
                result['success'] = True
                log(f"[{target_ip}] Deployment complete")
                
            except Exception as e:
                log(f"[{target_ip}] Deployment failed: {e}")
                result['error'] = str(e)
                
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
            container_ports = DeploymentPortResolver.get_container_ports(service_name, dockerfile)
            
            if not container_ports:
                # No ports - likely a one-time job
                log(f"[{server_ip}] No ports detected - checking container status")
                time.sleep(5)
                
                try:
                    # First check if container is still running
                    status_result = CommandExecuter.run_cmd(
                        f"docker ps --filter 'name={container_name}' --format '{{{{.Status}}}}'",
                        server_ip, "root"
                    )
                    status = status_result.stdout.strip() if hasattr(status_result, 'stdout') else str(status_result).strip()
                    
                    if status and 'Up' in status:
                        # Container still running - treat as success (long-running job)
                        log(f"[{server_ip}] Container still running - treating as healthy")
                        return True
                    
                    exit_code = DockerExecuter.get_container_exit_code(container_name, server_ip, "root")

                    if exit_code == -1:
                        log(f"[{server_ip}] Could not determine exit code for {container_name}")
                        return False
                    elif exit_code in [0, 1, 2, 3]:
                        log(f"[{server_ip}] One-time job completed successfully (exit code {exit_code})")
                        return True
                    else:
                        log(f"[{server_ip}] One-time job failed (exit code {exit_code})")
                        return False
                            
                except Exception as e:
                    log(f"[{server_ip}] Health check exception: {e}")
                    return False
                    
            else:
                # TCP service - check if container is running
                log(f"[{server_ip}] TCP service - verifying container is running")
                time.sleep(5)
                
                try:
                    result = CommandExecuter.run_cmd(
                        f"docker ps --filter 'name={container_name}' --format '{{{{.Status}}}}'",
                        server_ip, "root"
                    )
                    status = result.stdout.strip() if hasattr(result, 'stdout') else str(result).strip()
                    
                    if status and 'Up' in status:
                        log(f"[{server_ip}] TCP service container running successfully")
                        return True
                    else:
                        log(f"[{server_ip}] Container not running: {status}")
                        return False
                except Exception as e:
                    log(f"[{server_ip}] Could not check container status: {e}")
                    return False

    def _cleanup_empty_servers(self, project: str, env: str):
            """
            Find servers with no services deployed and destroy/release them.
            
            This implements the cleanup phase of your plan:
            "Find all IPs where no service is deployed and destroy/put them back to reserve"
            
            A server is considered "empty" only if:
            1. No running containers for this project/env, AND
            2. No scheduled cron jobs for this project/env (excluding health_monitor)
            
            Args:
                project: Project name
                env: Environment name
            """
            from cron_manager import CronManager
            
            all_servers = ServerInventory.list_all_servers()
            container_pattern = f"{project}_{env}_"
            
            empty_servers = []
            
            for server in all_servers:
                server_ip = server['ip']
                
                try:
                    # Check 1: Running containers
                    result = CommandExecuter.run_cmd(
                        f"docker ps --filter 'name={container_pattern}' --format '{{{{.Names}}}}'",
                        server_ip,
                        'root'
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
                        project=project,
                        env=env,
                        server_ip=server_ip,
                        user='root'
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
                    log(f"Server {server_ip} has no {project}/{env} services (containers or cron jobs)")
                        
                except Exception as e:
                    log(f"Could not check {server_ip}: {e}")
                    continue
            
            if empty_servers:
                # Check if we should destroy or just move to reserve
                # Use keep_reserve setting from config
                destroy_empty = not self.deployment_configurer.raw_config.get('project', {}).get('keep_reserve', False)
                
                if destroy_empty:
                    log(f"Destroying {len(empty_servers)} empty servers: {empty_servers}")
                    ServerInventory.release_servers(empty_servers, destroy=True)
                else:
                    log(f"Returning {len(empty_servers)} empty servers to reserve pool: {empty_servers}")
                    ServerInventory.release_servers(empty_servers, destroy=False)
            else:
                log("No empty servers found")

    def _get_servers_running_service(self, project: str, env: str, service_name: str) -> List[str]:
        """
        Get list of server IPs that have containers for this service.
        
        Args:
            project: Project name
            env: Environment name
            service_name: Service name
            
        Returns:
            List of server IPs that have containers for this service
        """
        all_servers = ServerInventory.list_all_servers()
        servers_with_service = []
        
        container_pattern = f"{project}_{env}_{service_name}"
        
        for server in all_servers:
            server_ip = server['ip']
            try:
                # Check if this server has containers for this service
                result = CommandExecuter.run_cmd(
                    f"docker ps -a --filter 'name={container_pattern}' --format '{{{{.Names}}}}'",
                    server_ip,
                    'root'
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

    def _cleanup_service_on_server(self, project: str, env: str, service_name: str, server_ip: str):
        """
        Stop and remove all containers for a service on a specific server.
        
        Args:
            project: Project name
            env: Environment name
            service_name: Service name
            server_ip: Server IP to clean up
        """
        container_pattern = f"{project}_{env}_{service_name}"
        
        try:
            # Find all containers for this service (base and secondary)
            result = CommandExecuter.run_cmd(
                f"docker ps -a --filter 'name={container_pattern}' --format '{{{{.Names}}}}'",
                server_ip,
                'root'
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
                    
        except Exception as e:
            log(f"Could not cleanup {service_name} on {server_ip}: {e}")

    def _deploy_immutable(
                self,
                project: str,
                env: str,
                service_name: str,
                service_config: Dict[str, Any]
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
                    
                    base_name = DeploymentNaming.get_container_name(project, env, service_name)
                    dockerfile = service_config.get("dockerfile")
                    container_ports = DeploymentPortResolver.get_container_ports(service_name, dockerfile)
                    container_port = container_ports[0] if container_ports else "8000"
                    base_port = DeploymentPortResolver.generate_host_port(project, env, service_name, container_port)
                    
                    toggle = self._determine_toggle(project, env, service_name, 'localhost', base_port, base_name)
                    new_name = toggle["name"]
                    
                    old_name = self._get_opposite_container_name(new_name, base_name)
                    if old_name:
                        try:
                            DockerExecuter.stop_and_remove_container(old_name, 'localhost', ignore_if_not_exists=True)
                        except Exception as e:
                            log(f"Note: Could not remove old container: {e}")
                    
                    try:
                        self.start_service(project, env, service_name, service_config, 'localhost')
                        all_zone_servers = [{'ip': 'localhost'}]
                        self._update_all_nginx_for_service(
                            project, env, service_name, ['localhost'], all_zone_servers
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
                            memory=memory
                        )
                        log(f"Created {len(new_ips)} new servers: {new_ips}")
                    except Exception as e:
                        log(f"Failed to claim servers: {e}")
                        Logger.end()
                        return False
                            
                # STEP 3: Calculate todel_ips (servers with service but not in target)
                current_service_servers = self._get_servers_running_service(project, env, service_name)
                
                # STEP 4: Calculate target_ips
                target_ips = green_ips + new_ips

                # Check if we have any servers
                if not target_ips:
                    log(f"ERROR: No servers available for {service_name} deployment")
                    Logger.end()
                    return False

                todel_ips = [ip for ip in current_service_servers if ip not in target_ip]
                
                if todel_ips:
                    log(f"Will remove {service_name} from these servers: {todel_ips}")
                
                log(f"Target deployment: {target_ips}")
                
                # STEP 5: Set success flag
                success = True
                deployed_servers = []
                
                # Calculate base naming (same for all servers)
                base_name = DeploymentNaming.get_container_name(project, env, service_name)
                dockerfile = service_config.get("dockerfile")
                container_ports = DeploymentPortResolver.get_container_ports(service_name, dockerfile)
                container_port = container_ports[0] if container_ports else "8000"
                base_port = DeploymentPortResolver.generate_host_port(project, env, service_name, container_port)
                
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
                            project, env, service_name, service_config,
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
                        ServerInventory.release_servers(new_ips, destroy=False)
                    
                    Logger.end()
                    return False
                
                # STEP 6d: Update nginx on ALL servers in zone (only if all deployments succeeded)
                log("All deployments successful - updating nginx configurations")
                
                all_zone_servers = self._get_all_servers_in_zone(zone)
                self._update_all_nginx_for_service(
                    project, env, service_name, deployed_servers, all_zone_servers
                )
                
                # STEP 7: Cleanup todel_ips (remove service from servers no longer in target)
                if todel_ips:
                    log(f"Cleaning up {service_name} from removed servers: {todel_ips}")
                    for ip in todel_ips:
                        self._cleanup_service_on_server(project, env, service_name, ip)
                
                # Record deployment state
                DeploymentStateManager.record_deployment(
                    project=project,
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

    def create_containers_network(self, env: str, server_ip: str = 'localhost', user: str = "root"):
        """Create Docker network for services communication"""
        network_name = DeploymentNaming.get_network_name(self.project_name, env)
        
        try:
            DockerExecuter.create_network(network_name, server_ip, user, ignore_if_exists=True)
            log(f"Network {network_name} ready on {server_ip}")
        except Exception as e:
            log(f"Warning: Could not create network {network_name} on {server_ip}: {e}")

    def start_service(
        self,
        project: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        server_ip: str = 'localhost',
        user: str = "root"
    ):
        """
        Start a service container (wrapper for compatibility).
        Determines if long-running or scheduled and calls appropriate method.
        """
        if self.is_service_scheduled(service_config):
            # For scheduled services, install the cron job
            return self.install_scheduled_service(project, env, service_name, service_config, server_ip)
        else:
            # For long-running services, start the container
            return self.start_long_running_service(project, env, service_name, service_config, server_ip, user)

    def start_long_running_service(
        self,
        project_name: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        server_ip: str = 'localhost',
        user: str = "root"
    ) -> bool:
        """
        Start a long-running service container.
        
        This method is called during deployment to start service containers.
        It handles network setup, volume mounting, and container lifecycle.
        """
        try:
            # Create network
            self.create_containers_network(env, server_ip, user)
            network_name = DeploymentNaming.get_network_name(project_name, env)
            
            # Get container name and image
            base_name = DeploymentNaming.get_container_name(project_name, env, service_name)
            
            # Get image (either custom or prebuilt)
            if service_config.get("image"):
                image = service_config["image"]
            else:
                docker_hub_user = self.deployment_configurer.get_docker_hub_user()
                version = self._get_version()
                image = DeploymentNaming.get_image_name(
                    docker_hub_user, project_name, env, service_name, version
                )
            
            # Pull image if remote
            if server_ip != 'localhost':
                log(f"Pulling image {image} to {server_ip}...")
                DockerExecuter.pull_image(image, server_ip, user)
            
            # Get volumes
            volumes = PathResolver.generate_all_volume_mounts(
                project_name, env, service_name,
                server_ip=server_ip,
                use_docker_volumes=True,
                user=user
            )
            
            # Get dockerfile for port detection
            dockerfile = service_config.get("dockerfile")
            container_ports = DeploymentPortResolver.get_container_ports(service_name, dockerfile)
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
                user=user
            )
            
            log(f"Started {service_name} on {server_ip}")
            return True
            
        except Exception as e:
            log(f"Failed to start {service_name} on {server_ip}: {e}")
            return False

    def install_scheduled_service(
            self,
            project_name: str,
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
                image = DeploymentNaming.get_image_name(
                    docker_hub_user, project_name, env, service_name, version
                )
            
            # Pull image if remote
            if server_ip != 'localhost':
                log(f"Pulling image {image} to {server_ip}...")
                DockerExecuter.pull_image(image, server_ip, "root")
            
            # Install via CronManager directly
            success = CronManager.install_cron_job(
                project=project_name,
                env=env,
                service_name=service_name,
                service_config=service_config,
                docker_hub_user=self.deployment_configurer.get_docker_hub_user(),
                version=self._get_version(),
                server_ip=server_ip,
                user="root"
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
        dockerfile_path = constants.get_dockerfiles_path() / f"Dockerfile.{self.project_name}-{env}-{service_name}.tmp"
        
        # Write initial content
        dockerfile_path.write_text(content)
        
        # Inject /app directories
        return self.inject_app_directories_to_dockerfile(str(dockerfile_path), service_name)

    def _update_nginx_for_new_servers(
        self,
        project: str,
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
        import os
        email = os.getenv("ADMIN_EMAIL")
        cloudflare_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
        
        # Update nginx on each new server
        for server_ip in new_server_ips:
            try:
                from nginx_config_generator import NginxConfigGenerator
                
                NginxConfigGenerator.setup_service(
                    project=project,
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
        from deployment_state_manager import DeploymentStateManager
        
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
        from deployment_state_manager import DeploymentStateManager
        
        # Get deployment info
        deployment = DeploymentStateManager.get_current_deployment(
            self.project_name, env, service
        )
        
        if not deployment:
            return f"No deployment found for {self.project_name}/{env}/{service}"
        
        container_name = deployment['container_name']
        servers = deployment['servers']
        
        if not servers:
            return "No servers found for this deployment"
        
        # Fetch logs from first server
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


    def _determine_toggle(
        self,
        project: str,
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
            project: Project name
            env: Environment
            service: Service name
            server_ip: Target server IP
            base_port: Base host port (e.g., 8357)
            base_name: Base container name
            
        Returns:
            {"port": 8357, "name": "base_name"} or
            {"port": 18357, "name": "base_name_secondary"}
        """
        existing = DockerExecuter.find_service_container(project, env, service, server_ip)
        
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
        project: str,
        env: str,
        service: str,
        deployed_servers: List[str]
    ) -> str:
        """
        Determine nginx backend mode FOR THIS SPECIFIC SERVICE.
        
        CRITICAL: This is per-service, not global!
        
        Decision:
        - Service on 1 server → "single_server" (use container names, no port mapping)
        - Service on 2+ servers → "multi_server" (use IPs + host ports)
        
        Args:
            project: Project name
            env: Environment
            service: Service name
            deployed_servers: Servers where THIS SERVICE is deployed
            
        Returns:
            "single_server" or "multi_server"
        """
        if len(deployed_servers) == 1:
            log(f"Backend mode for {service}: single_server (deployed on {deployed_servers[0]} only)")
            return "single_server"
        else:
            log(f"Backend mode for {service}: multi_server (deployed on {len(deployed_servers)} servers)")
            return "multi_server"


    def _generate_nginx_backends(
        self,
        mode: str,
        project: str,
        env: str,
        service: str,
        deployed_servers: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Generate backend list for nginx based on deployment mode.
        
        Args:
            mode: "single_server" or "multi_server"
            project: Project name
            env: Environment
            service: Service name
            deployed_servers: Servers where THIS SERVICE is deployed
            
        Returns:
            Single-server: [{"container_name": "...", "port": "5432"}, ...]
            Multi-server: [{"ip": "...", "port": 8357}, ...]
        """
        if mode == "single_server":
            # Use container names (Docker DNS)
            backends = []
            server_ip = deployed_servers[0]
            
            # Get both primary and secondary containers if they exist
            for suffix in ["", "_secondary"]:
                container_name = DeploymentNaming.get_container_name(project, env, service)
                if suffix:
                    container_name = f"{container_name}{suffix}"
                
                # Check if container exists
                if DockerExecuter.container_exists(container_name, server_ip):
                    # Get container port (not host port!)
                    service_config = self.deployment_configurer.get_services(env)[service]
                    dockerfile = service_config.get("dockerfile")
                    container_ports = DeploymentPortResolver.get_container_ports(service, dockerfile)
                    container_port = container_ports[0] if container_ports else "8000"
                    
                    backends.append({
                        "container_name": container_name,
                        "port": container_port  # Container port (5432, not 8357)
                    })
            
            return backends
        
        else:  # multi_server
            # Use IP + host ports
            backends = []
            
            for server_ip in deployed_servers:
                # Find what's actually running on this server
                base_name = DeploymentNaming.get_container_name(project, env, service)
                
                # Check for both primary and secondary
                for suffix in ["", "_secondary"]:
                    container_name = f"{base_name}{suffix}" if suffix else base_name
                    
                    if DockerExecuter.container_exists(container_name, server_ip):
                        # Get published host port
                        port_map = DockerExecuter.get_published_ports(container_name, server_ip)
                        
                        # Find the host port (format: "5432/tcp" -> "8357")
                        service_config = self.deployment_configurer.get_services(env)[service]
                        dockerfile = service_config.get("dockerfile")
                        container_ports = DeploymentPortResolver.get_container_ports(service, dockerfile)
                        container_port = container_ports[0] if container_ports else "8000"
                        
                        port_key = f"{container_port}/tcp"
                        if port_key in port_map:
                            host_port = port_map[port_key]
                            
                            backends.append({
                                "ip": server_ip,
                                "port": host_port  # Host port (8357 or 18357)
                            })
            
            return backends


    def _update_all_nginx_for_service(
        self,
        project: str,
        env: str,
        service: str,
        deployed_servers: List[str],
        all_zone_servers: List[Dict[str, Any]]
    ) -> None:
        """
        Update nginx stream config on all servers in zone FOR THIS SERVICE.
        
        CRITICAL: Mode is determined per-service, not globally!
        
        This allows mixed deployments where some services are single-server
        and others are multi-server within the same zone.
        
        Args:
            project: Project name
            env: Environment
            service: Service name
            deployed_servers: Servers where THIS SERVICE is deployed
            all_zone_servers: All servers in the zone (for nginx updates)
        """
        if not self._is_tcp_service(service):
            log(f"Skipping nginx stream config for {service} (not a TCP service)")
            return
        
        # Determine mode FOR THIS SPECIFIC SERVICE
        mode = self._determine_backend_mode_for_service(
            project, env, service, deployed_servers
        )
        
        log(f"Updating nginx stream config for {service} (mode: {mode})")
        
        # Generate backends based on THIS SERVICE's deployment
        backends = self._generate_nginx_backends(
            mode, project, env, service, deployed_servers
        )
        
        if not backends:
            log(f"No backends found for {service}")
            return
        
        # Calculate internal port (stable for this service)
        internal_port = DeploymentPortResolver.get_internal_port(project, env, service)
        log(f"Internal port for {service}: {internal_port}")
        
        # Update nginx on EVERY server in the zone
        # (Even servers that don't run this service need the config)
        for server in all_zone_servers:
            try:
                NginxConfigGenerator.update_stream_config_on_server(
                    server['ip'], project, env, service, backends, internal_port, mode
                )
            except Exception as e:
                log(f"Warning: Failed to update nginx on {server['ip']}: {e}")


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
        Get all green servers in a zone.
        
        Args:
            zone: Zone name
            
        Returns:
            List of server dicts
        """
        return ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_ACTIVE,
            zone=zone
        )


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
        
    def pre_provision_servers(self, env: str, service_name: str = None) -> Dict[str, List[str]]:
        """
        Pre-provision all servers needed for deployment based on service requirements.
        
        This analyzes all services that will be deployed and provisions all required
        servers upfront in parallel, making the actual deployment much faster.
        
        Args:
            env: Environment to provision for
            service_name: Optional specific service, otherwise all services
            
        Returns:
            Dictionary mapping "cpu_memory_zone" -> list of provisioned server IPs
            
        Example:
            # Call this before deploy() for faster deployment
            deployer.pre_provision_servers(env="prod")
            deployer.deploy(env="prod")
        """
        project = self.deployment_configurer.get_project_name()
        log(f"Pre-provisioning servers for {project}/{env}")
        
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
                        ]
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