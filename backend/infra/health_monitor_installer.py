"""
Health Monitor Installer - MINIMAL VERSION
Ships only essential runtime files to droplets for security.

SECURITY IMPROVEMENTS:
- Only copies 20 runtime files (not entire infra folder)
- Excludes all deployment orchestration logic
- Excludes nginx generation logic
- Excludes build/sync logic
"""

from pathlib import Path
import tempfile
import shutil

try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .cron_manager import CronManager
except ImportError:
    from cron_manager import CronManager
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .deployment_syncer import DeploymentSyncer
except ImportError:
    from deployment_syncer import DeploymentSyncer
try:
    from .credentials_manager import CredentialsManager
except ImportError:
    from credentials_manager import CredentialsManager


def log(msg):
    Logger.log(msg)


class HealthMonitorInstaller:
    """Lightweight installer for health monitor on droplets"""
    
    # Health monitor Dockerfile configuration
    DOCKERFILE_CONTENT = {
        "1": "FROM python:3.11-slim",
        "2": "WORKDIR /app",
        "3": "COPY . /app/",
        "4": "RUN pip install --no-cache-dir requests",
        "5": "CMD [\"python\", \"/app/health_monitor.py\"]"
    }
    
    IMAGE_NAME = "health-monitor:latest"
    SCHEDULE = "* * * * *"  # Every minute
    
    # =========================================================================
    # MINIMAL FILE WHITELIST - Only ship what's needed for runtime monitoring
    # =========================================================================
    REQUIRED_FILES = [
        # Core monitoring
        'health_monitor.py',              # Main monitoring logic
        'logger.py',                      # Logging
        
        # Constants
        'deployment_constants.py',        # Config file names/paths
        
        # Agent communication
        'agent_deployer.py',              # HTTP calls to health agent
        
        # Infrastructure queries (read-only)
        'server_inventory.py',            # Query server inventory
        'do_manager.py',                  # DigitalOcean API calls
        'do_state_manager.py',            # Query DO droplet state
        'live_deployment_query.py',       # Query running containers
        
        # Configuration readers (read-only)
        'deployment_config.py',           # Read deployment.json
        'credentials_manager.py',         # Read credentials
        'path_resolver.py',               # Path calculations
        'resource_resolver.py',           # Service discovery
        'deployment_naming.py',           # Container naming conventions
        
        # Nginx config reading (read-only)
        'nginx_config_parser.py',         # Parse nginx configs
        
        # Execution utilities
        'execute_cmd.py',                 # SSH command execution
        'execute_docker.py',              # Docker commands
        
        # Auto-scaling
        'auto_scaling_coordinator.py',    # Scaling decisions
        'deployment_state_manager.py',    # State management
        'deployment_port_resolver.py',    # Port lookups (read-only)
        
        # SSL (for multi-zone)
        'certificate_manager.py',         # SSL cert management
        
        # Environment
        'env_loader.py',                  # Load .env file
    ]
    
    # =========================================================================
    # EXCLUDED FILES - Proprietary logic NOT shipped to droplets
    # =========================================================================
    # deployer.py                - Deployment orchestration
    # project_deployer.py        - Project setup
    # deployment_syncer.py       - File sync logic
    # docker_executer.py         - Image building
    # nginx_config_generator.py  - Config generation (templates exposed)
    # cron_manager.py            - Cron scheduling
    # health_monitor_installer.py - This file itself
    # health_agent_installer.py  - Agent setup
    # backup_manager.py          - Backup logic
    # All other files...
    
    @staticmethod
    def install_on_server(server_ip: str, user: str = "root") -> bool:
        """
        Install health monitor on a single server.
        
        MINIMAL VERSION: Only copies required runtime files.
        
        Process:
        1. Copy ONLY required files to build directory
        2. Transfer to server
        3. Build Docker image
        4. Schedule as cron job
        
        Args:
            server_ip: Target server IP
            user: SSH user
            
        Returns:
            True if installation successful
        """
        log(f"Installing MINIMAL health monitor on {server_ip}...")
        log(f"Shipping {len(HealthMonitorInstaller.REQUIRED_FILES)} files (not entire infra folder)")
        Logger.start()
        
        try:
            # 1. Create temporary directory with ONLY required files            
            project_dir = Path(__file__).parent
            
            # Create temp directory to hold files to transfer
            with tempfile.TemporaryDirectory() as temp_dir_str:
                temp_dir = Path(temp_dir_str)
                build_dir = temp_dir / "health_monitor_build"
                build_dir.mkdir()
                
                # Copy ONLY whitelisted files
                copied_files = 0
                missing_files = []
                
                for filename in HealthMonitorInstaller.REQUIRED_FILES:
                    src = project_dir / filename
                    
                    if src.exists():
                        if src.is_file():
                            shutil.copy2(src, build_dir / filename)
                            copied_files += 1
                        else:
                            log(f"Warning: {filename} is a directory, skipping")
                    else:
                        missing_files.append(filename)
                        log(f"Warning: {filename} not found, skipping")
                
                log(f"Copied {copied_files}/{len(HealthMonitorInstaller.REQUIRED_FILES)} files")
                
                if missing_files:
                    log(f"Missing files: {', '.join(missing_files)}")
                
                # Generate and add Dockerfile
                dockerfile_text = HealthMonitorInstaller._generate_dockerfile()
                (build_dir / "Dockerfile").write_text(dockerfile_text, encoding='utf-8')
                
                log(f"Prepared minimal project files for transfer")
                
                # 2. Use DeploymentSyncer.push_directory to transfer files                
                success = DeploymentSyncer.push_directory(
                    local_dir=build_dir,
                    remote_base_path="/tmp",
                    server_ips=[server_ip],
                    set_permissions=False,
                    parallel=False
                )
                
                if not success:
                    raise Exception("Failed to transfer project files to server")
                
                log(f"Minimal files transferred to server")
            
            # 3. Build Docker image
            log(f"Building health monitor image on {server_ip}...")
            CommandExecuter.run_cmd(
                f"docker build -t {HealthMonitorInstaller.IMAGE_NAME} /tmp/health_monitor_build",
                server_ip, user
            )
            
            # 4. Cleanup build directory
            CommandExecuter.run_cmd("rm -rf /tmp/health_monitor_build", server_ip, user)
            
            log(f"Built minimal health monitor image")
            
            # 5. Schedule with CronManager
            service_config = {
                "schedule": HealthMonitorInstaller.SCHEDULE,
                "image": HealthMonitorInstaller.IMAGE_NAME,
                "env_vars": {},
                "volumes": [
                    "/local:/app/local:ro"  # Mount entire /local tree read-only
                ]                
            }
            
            success = CronManager.install_cron_job(
                project="health",
                env="monitor",
                service_name="system",
                service_config=service_config,
                docker_hub_user="",
                version="latest",
                server_ip=server_ip,
                user=user
            )            

            if not success:
                log(f"Warning: Failed to schedule health monitor on {server_ip}")
                Logger.end()
                return False
            
            Logger.end()
            log(f"✓ Minimal health monitor installed on {server_ip}")
            log(f"✓ Excluded proprietary deployment/build/sync logic")
            return True
            
        except Exception as e:
            log(f"Failed to install health monitor on {server_ip}: {e}")
            Logger.end()
            return False

    @staticmethod
    def _generate_dockerfile() -> str:
        """Generate Dockerfile from DOCKERFILE_CONTENT dict"""
        # Sort by numeric keys
        def sort_key(key):
            parts = key.split('.')
            return [int(part) for part in parts]
        
        sorted_keys = sorted(HealthMonitorInstaller.DOCKERFILE_CONTENT.keys(), key=sort_key)
        
        lines = []
        for key in sorted_keys:
            lines.append(HealthMonitorInstaller.DOCKERFILE_CONTENT[key])
        
        return '\n'.join(lines)
    
    @staticmethod
    def remove_from_server(server_ip: str, user: str = "root") -> bool:
        """Remove health monitor from server"""
        log(f"Removing health monitor from {server_ip}...")
        
        try:
            # Remove cron job
            CronManager.remove_cron_job(
                project="health",
                env="monitor", 
                service_name="system",
                server_ip=server_ip,
                user=user
            )
            
            # Remove Docker image
            CommandExecuter.run_cmd(
                f"docker rmi {HealthMonitorInstaller.IMAGE_NAME}",
                server_ip, user
            )
            
            log(f"Health monitor removed from {server_ip}")
            return True
            
        except Exception as e:
            log(f"Failed to remove health monitor from {server_ip}: {e}")
            return False