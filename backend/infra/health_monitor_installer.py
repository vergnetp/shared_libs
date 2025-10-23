from pathlib import Path
from typing import Dict, Any
import os
import tempfile
import shutil

from execute_cmd import CommandExecuter
from cron_manager import CronManager
from logger import Logger
from deployment_syncer import DeploymentSyncer


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
    
    @staticmethod
    def install_on_server(server_ip: str, user: str = "root") -> bool:
        """
        Install health monitor on a single server.
        
        Process:
        1. Copy project files to server
        2. Build Docker image
        3. Schedule as cron job
        
        Args:
            server_ip: Target server IP
            user: SSH user
            
        Returns:
            True if installation successful
        """
        log(f"Installing health monitor on {server_ip}...")
        Logger.start()
        
        try:
            # 1. Create temporary directory with project files            
            project_dir = Path(__file__).parent
            
            # Create temp directory to hold files to transfer
            with tempfile.TemporaryDirectory() as temp_dir_str:
                temp_dir = Path(temp_dir_str)
                build_dir = temp_dir / "health_monitor_build"
                build_dir.mkdir()
                
                # Copy project files (excluding certain directories)
                for item in project_dir.iterdir():
                    if item.name in {'__pycache__', 'deployments', 'local', '.git'}:
                        continue
                    
                    if item.is_file():
                        shutil.copy2(item, build_dir / item.name)
                    elif item.is_dir():
                        shutil.copytree(item, build_dir / item.name, 
                                      ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                
                # Generate and add Dockerfile
                dockerfile_text = HealthMonitorInstaller._generate_dockerfile()
                (build_dir / "Dockerfile").write_text(dockerfile_text, encoding='utf-8')
                
                log(f"Prepared project files for transfer")
                
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
                
                log(f"Project files transferred to server")
            
            # 3. Build Docker image
            log(f"Building health monitor image on {server_ip}...")
            CommandExecuter.run_cmd(
                f"docker build -t {HealthMonitorInstaller.IMAGE_NAME} /tmp/health_monitor_build",
                server_ip, user
            )
            
            # 4. Cleanup build directory
            CommandExecuter.run_cmd("rm -rf /tmp/health_monitor_build", server_ip, user)
            
            log(f"Built health monitor image")
            
            # 5. Schedule with CronManager
            service_config = {
                "schedule": HealthMonitorInstaller.SCHEDULE,
                "image": HealthMonitorInstaller.IMAGE_NAME,
                "env_vars": {},
                "network_name": None  # Health monitor doesn't need a project network
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
            log(f"Health monitor installed on {server_ip}")
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