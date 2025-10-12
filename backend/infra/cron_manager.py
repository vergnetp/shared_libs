from typing import Dict, Any, List, Optional
from execute_cmd import CommandExecuter
from deployment_naming import DeploymentNaming
from deployment_syncer import DeploymentSyncer
from logger import Logger
from path_resolver import PathResolver

def log(msg):
    Logger.log(msg)


class CronManager:
    """Manages cron jobs for scheduled Docker containers"""

    @staticmethod
    def generate_cron_entry(
        project: str, 
        env: str, 
        service_name: str, 
        service_config: Dict[str, Any],
        docker_hub_user: str,        
        server_ip: str,
        user: str = "root",
        version: str = "latest",
    ) -> Optional[str]:
        """
        Generate a cron entry for a scheduled service.
        
        Args:
            project: Project name
            env: Environment name  
            service_name: Service name
            service_config: Service configuration dict
            docker_hub_user: Docker Hub username
            server_ip: Target ip of the server (or 'localhost')
            user: the Target server user. Default to 'root'
            version: Image version
            
        Returns:
            Cron entry string or None if service is not scheduled
        """
        schedule = service_config.get("schedule")
        if not schedule:
            return None
            
        # Get image name - use provided image or generate build name
        if service_config.get("image"):
            image = service_config["image"]
        else:
            image = DeploymentNaming.get_image_name(
                docker_hub_user,
                project,
                env,
                service_name,
                version                
            )
        
        # Generate unique container name with timestamp to avoid conflicts
        base_container_name = DeploymentNaming.get_container_name(project, env, service_name)
        container_name = f"{base_container_name}_$(date +%Y%m%d_%H%M%S)"
        
        # Get network name
        network_name = DeploymentNaming.get_network_name(project, env)
        
        # Build docker run command
        docker_cmd_parts = ["docker", "run", "--rm", "--name", container_name]
        
        # Add network
        if network_name:
            docker_cmd_parts.extend(["--network", network_name])
        
        # Add volumes
        volumes = CronManager._prepare_volumes_for_cron(project, env, service_name, service_config, server_ip, user)
        for volume in volumes:
            docker_cmd_parts.extend(["-v", volume])
        
        # Add environment variables
        env_vars = service_config.get("env_vars", {})
        for key, value in env_vars.items():
            docker_cmd_parts.extend(["-e", f"{key}={value}"])
        
        # Add image
        docker_cmd_parts.append(image)
        
        # Add custom command if specified
        command = service_config.get("command")
        if command:
            if isinstance(command, list):
                docker_cmd_parts.extend(command)
            else:
                docker_cmd_parts.append(command)
        
        # Build complete docker command
        docker_cmd = " ".join(f'"{part}"' if " " in str(part) else str(part) for part in docker_cmd_parts)
        
        # Create cron entry with logging
        log_file = f"/var/log/cron_{project}_{env}_{service_name}.log"
        cron_entry = f"{schedule} {docker_cmd} >> {log_file} 2>&1"
        
        return cron_entry

    @staticmethod
    def _prepare_volumes_for_cron(project: str, env: str, service_name: str, service_config: Dict[str, Any], server_ip: str, user: str = "root") -> List[str]:
        """Prepare volumes for cron job containers"""
        volumes = []
        
        # Check if service config has explicit volumes
        volume_config = service_config.get("volumes", [])
        
        if volume_config:
            # Use explicitly configured volumes
            if isinstance(volume_config, dict):
                for host_path, container_path in volume_config.items():
                    volumes.append(f"{host_path}:{container_path}")
            elif isinstance(volume_config, list):
                volumes = volume_config
        else:
            # Use auto-generated volumes from PathResolver
            volumes = PathResolver.generate_all_volume_mounts(
                project, env, service_name, server_ip,
                use_docker_volumes=True, user=user
            )
        
        return volumes

    @staticmethod
    def get_cron_identifier(project: str, env: str, service_name: str) -> str:
        """Generate unique identifier for cron job management"""
        return f"# MANAGED_CRON_{project}_{env}_{service_name}"

    @staticmethod
    def install_cron_job(
        project: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        docker_hub_user: str,
        version: str = "latest",
        server_ip: str = "localhost",
        user: str = "root"
    ) -> bool:
        """
        Install or update cron job for a scheduled service.
        
        Returns:
            True if cron job was installed/updated successfully
        """
        cron_entry = CronManager.generate_cron_entry(
            project, env, service_name, service_config, docker_hub_user, server_ip, user, version
        )
        
        if not cron_entry:
            log(f"No schedule defined for {service_name}, skipping cron installation")
            return False
        
        identifier = CronManager.get_cron_identifier(project, env, service_name)
        
        try:
            # Remove existing cron job if it exists
            CronManager.remove_cron_job(project, env, service_name, server_ip, user)
            
            # Add new cron job
            full_cron_entry = f"{identifier}\n{cron_entry}"
            
            # Create temporary cron file
            temp_cron_file = f"/tmp/cron_temp_{project}_{env}_{service_name}"
            
            # Escape the cron entry for shell
            escaped_cron_entry = full_cron_entry.replace("'", "'\\''")

            # Get current crontab, add new entry, and install
            commands = [
                f"crontab -l 2>/dev/null > {temp_cron_file} || touch {temp_cron_file}",
                f"echo '{escaped_cron_entry}' >> {temp_cron_file}",  # Use single quotes
                f"crontab {temp_cron_file}",
                f"rm {temp_cron_file}"
            ]
            
            for cmd in commands:
                CommandExecuter.run_cmd(cmd, server_ip, user)
            
            log(f"Installed cron job for {service_name}: {service_config.get('schedule')}")
            return True
            
        except Exception as e:
            log(f"Failed to install cron job for {service_name}: {e}")
            return False

    @staticmethod
    def remove_cron_job(
        project: str,
        env: str,
        service_name: str,
        server_ip: str = "localhost",
        user: str = "root"
    ) -> bool:
        """
        Remove cron job for a service.
        
        Returns:
            True if cron job was removed successfully or didn't exist
        """
        identifier = CronManager.get_cron_identifier(project, env, service_name)
        
        try:
            temp_cron_file = f"/tmp/cron_temp_remove_{project}_{env}_{service_name}"

            # Export current crontab, remove lines with our identifier, and reinstall
            commands = [
                f"crontab -l 2>/dev/null > {temp_cron_file} || touch {temp_cron_file}",
                f"grep -v '{identifier}' {temp_cron_file} > {temp_cron_file}.final || touch {temp_cron_file}.final",
                f"crontab {temp_cron_file}.final",
                f"rm {temp_cron_file} {temp_cron_file}.clean {temp_cron_file}.final 2>/dev/null || true"
            ]
            
            for cmd in commands:
                CommandExecuter.run_cmd(cmd, server_ip, user)
            
            log(f"Removed cron job for {service_name}")
            return True
            
        except Exception as e:
            log(f"Failed to remove cron job for {service_name}: {e}")
            return False

    @staticmethod
    def list_managed_cron_jobs(
        project: str,
        env: str,
        server_ip: str = "localhost",
        user: str = "root"
    ) -> List[str]:
        """
        List all managed cron jobs for a project/environment.
        
        Returns:
            List of cron entries
        """
        try:
            result = CommandExecuter.run_cmd("crontab -l", server_ip, user)
            crontab_content = result.stdout if hasattr(result, 'stdout') else str(result)
            
            managed_jobs = []
            lines = crontab_content.split('\n')
            
            for i, line in enumerate(lines):
                if f"# MANAGED_CRON_{project}_{env}" in line:
                    # Next line should be the actual cron job
                    if i + 1 < len(lines):
                        managed_jobs.append(lines[i + 1])
            
            return managed_jobs
            
        except Exception as e:
            log(f"Failed to list cron jobs: {e}")
            return []

    @staticmethod
    def cleanup_old_containers(
        project: str,
        env: str,
        service_name: str,
        server_ip: str = "localhost",
        user: str = "root"
    ) -> None:
        """Clean up old scheduled containers that may not have been removed"""
        try:
            base_name = DeploymentNaming.get_container_name(project, env, service_name)
            
            # Find containers with our naming pattern
            cmd = f'docker ps -a --filter "name={base_name}_" --format "{{{{.Names}}}}"'
            result = CommandExecuter.run_cmd(cmd, server_ip, user)
            
            container_names = []
            if hasattr(result, 'stdout'):
                container_names = [name.strip() for name in result.stdout.split('\n') if name.strip()]
            else:
                container_names = [name.strip() for name in str(result).split('\n') if name.strip()]
            
            for container_name in container_names:
                if container_name:
                    try:
                        CommandExecuter.run_cmd(f"docker rm -f {container_name}", server_ip, user)
                        log(f"Cleaned up old container: {container_name}")
                    except:
                        pass  # Container may already be removed
                        
        except Exception as e:
            log(f"Warning: Failed to cleanup old containers for {service_name}: {e}")

    @staticmethod
    def validate_cron_schedule(schedule: str) -> bool:
        """
        Validate cron schedule format.
        
        Args:
            schedule: Cron schedule string (e.g., "*/10 * * * *")
            
        Returns:
            True if schedule format appears valid
        """
        if not schedule or not isinstance(schedule, str):
            return False
        parts = schedule.strip().split()
        if len(parts) not in (5, 6):  # support both 5 and 6 fields
            return False
        valid_chars = set('0123456789*/,-')
        for part in parts:
            if not all(c in valid_chars for c in part):
                return False
        return True