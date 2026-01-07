import platform
import subprocess
import getpass
import os
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .resource_resolver import ResourceResolver
except ImportError:
    from resource_resolver import ResourceResolver
try:
    from .cron_manager import CronManager
except ImportError:
    from cron_manager import CronManager


def log(msg):
    Logger.log(msg)


class PlatformScheduler:
    """Cross-platform scheduling abstraction layer"""
    
    @staticmethod
    def detect_platform_and_scheduler(server_ip: str = "localhost", user: str = "root") -> Tuple[str, str]:
        """
        Detect target platform and available scheduler.
        
        Returns:
            Tuple of (platform, scheduler) where:
            - platform: 'windows', 'linux', 'macos'  
            - scheduler: 'cron', 'schtasks', 'none'
        """
        if server_ip == "localhost":
            # Local detection
            system = platform.system().lower()
            if system == "windows":
                return ("windows", PlatformScheduler._check_windows_scheduler())
            elif system == "darwin":
                return ("macos", PlatformScheduler._check_unix_scheduler())
            else:
                return ("linux", PlatformScheduler._check_unix_scheduler())
        else:
            # Remote detection via SSH
            return PlatformScheduler._detect_remote_platform(server_ip, user)
    
    @staticmethod
    def _check_windows_scheduler() -> str:
        """Check Windows Task Scheduler availability"""
        try:
            result = subprocess.run(['schtasks', '/query'], 
                                  capture_output=True, text=True, check=False)
            return "schtasks" if result.returncode == 0 else "none"
        except FileNotFoundError:
            return "none"
    
    @staticmethod
    def _check_unix_scheduler() -> str:
        """Check Unix-like cron availability"""
        try:
            result = subprocess.run(['crontab', '-l'], 
                                  capture_output=True, text=True, check=False)
            return "cron" if result.returncode == 0 or "no crontab" in result.stderr.lower() else "none"
        except FileNotFoundError:
            # Try alternative cron commands
            for cmd in ['crond', 'cron']:
                try:
                    subprocess.run(['which', cmd], capture_output=True, check=True)
                    return "cron"
                except:
                    continue
            return "none"
    
    @staticmethod
    def _detect_remote_platform(server_ip: str, user: str) -> Tuple[str, str]:
        """Detect remote platform and scheduler via SSH"""
        try:
            # Detect OS
            result = CommandExecuter.run_cmd("uname -s", server_ip, user)
            os_name = result.stdout.strip().lower() if hasattr(result, 'stdout') else str(result).lower()
            
            if 'linux' in os_name:
                platform_name = "linux"
            elif 'darwin' in os_name:
                platform_name = "macos"  
            else:
                platform_name = "unknown"
            
            # Check for cron
            try:
                CommandExecuter.run_cmd("crontab -l", server_ip, user)
                scheduler = "cron"
            except:
                try:
                    CommandExecuter.run_cmd("which crontab", server_ip, user)
                    scheduler = "cron"
                except:
                    scheduler = "none"
            
            return (platform_name, scheduler)
            
        except Exception as e:
            log(f"Warning: Could not detect remote platform on {server_ip}: {e}")
            try:
                # Try Windows detection
                CommandExecuter.run_cmd("schtasks /query", server_ip, user)
                return ("windows", "schtasks")
            except:
                return ("unknown", "none")


class WindowsTaskScheduler:
    """Windows Task Scheduler implementation"""
    
    @staticmethod
    def create_scheduled_task(
        project: str,
        env: str, 
        service_name: str,
        service_config: Dict[str, Any],
        docker_hub_user: str,
        version: str = "latest",
        server_ip: str = "localhost",
        user: str = "Administrator"
    ) -> bool:
        """Create Windows scheduled task for Docker container"""
        
        # Try simple schtasks approach first - most reliable
        log("Attempting simple schtasks task creation...")
        try:
            if WindowsTaskScheduler._create_simple_schtasks_task(
                project, env, service_name, service_config, docker_hub_user, version, server_ip, user
            ):
                return True
        except Exception as e:
            log(f"Simple schtasks method failed: {e}")
        
        # Try complex schtasks as fallback
        log("Simple schtasks failed, trying complex schtasks...")
        try:
            if WindowsTaskScheduler._create_scheduled_task_schtasks(
                project, env, service_name, service_config, docker_hub_user, version, server_ip, user
            ):
                return True
        except Exception as e:
            log(f"Complex schtasks method failed: {e}")
        
        log("All Windows task creation methods failed")
        return False
    
    @staticmethod
    def _create_simple_schtasks_task(
        project: str,
        env: str, 
        service_name: str,
        service_config: Dict[str, Any],
        docker_hub_user: str,
        version: str = "latest",
        server_ip: str = "localhost",
        user: str = "Administrator"
    ) -> bool:
        """Create Windows scheduled task using simple schtasks command"""
        
        try:
            # Create task name
            task_name = f"{project}_{env}_{service_name}"
            
            # Generate Docker command
            docker_cmd = WindowsTaskScheduler._generate_docker_command(
                project, env, service_name, service_config, docker_hub_user, version
            )
            
            # Write batch script wrapper
            script_path = Path(f"C:/deployments/{project}/{env}/{service_name}_task.bat")
            script_content = f"""@echo off
{docker_cmd}
"""
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text(script_content, encoding="utf-8")
            log(f"Created batch script: {script_path}")
            
            # Get current user for task execution
            current_user = os.environ.get('USERNAME', getpass.getuser())
            
            # Simple schtasks command - run every minute with current user
            simple_cmd = f'schtasks /create /tn "{task_name}" /tr "{script_path}" /sc minute /mo 1 /ru "{current_user}" /f'
            
            log(f"Executing simple command: {simple_cmd}")
            result = CommandExecuter.run_cmd(simple_cmd, server_ip, user)
            
            # Check for success indicators
            result_str = str(result).upper()
            if "SUCCESS" in result_str or "SUCCESSFULLY" in result_str or result_str.strip() == "":
                log(f"Successfully created Windows scheduled task: {task_name}")
                
                # Verify task was created
                verify_cmd = f'schtasks /query /tn "{task_name}"'
                try:
                    verify_result = CommandExecuter.run_cmd(verify_cmd, server_ip, user)
                    if task_name in str(verify_result):
                        log(f"Task verification successful: {task_name}")
                        return True
                    else:
                        log(f"Task verification failed: {task_name}")
                        return False
                except:
                    log(f"Task verification failed but creation may have succeeded: {task_name}")
                    return True  # Assume success if we can't verify
            else:
                log(f"Simple schtasks command failed: {result}")
                return False
                
        except Exception as e:
            log(f"Exception in simple schtasks method: {e}")
            return False
    
    @staticmethod
    def _create_scheduled_task_schtasks(
        project: str,
        env: str, 
        service_name: str,
        service_config: Dict[str, Any],
        docker_hub_user: str,
        version: str = "latest",
        server_ip: str = "localhost",
        user: str = "Administrator"
    ) -> bool:
        """Create Windows scheduled task using schtasks command (fallback method)"""
        
        # Parse cron to Windows schedule
        schedule = service_config.get("schedule")
        if not schedule:
            return False
            
        windows_schedule = WindowsTaskScheduler._convert_cron_to_windows(schedule)
        if not windows_schedule:
            log(f"Cannot convert cron schedule '{schedule}' to Windows format")
            return False
        
        # Generate Docker command
        docker_cmd = WindowsTaskScheduler._generate_docker_command(
            project, env, service_name, service_config, docker_hub_user, version
        )
        
        # Create task name
        task_name = f"{project}_{env}_{service_name}"

        # Write batch script wrapper (avoid /tr length limit)
        script_path = Path(f"C:/deployments/{project}/{env}/{service_name}_task.bat")
        script_content = f"""@echo off
{docker_cmd}
"""
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_content, encoding="utf-8")

        # Try different approaches based on user context
        success = False
        approaches = [
            WindowsTaskScheduler._try_current_user,
            WindowsTaskScheduler._try_administrator_user,
            WindowsTaskScheduler._try_system_user_with_password
        ]
        
        for approach in approaches:
            try:
                if approach(task_name, script_path, windows_schedule, user, server_ip):
                    log(f"Created Windows scheduled task: {task_name}")
                    success = True
                    break
            except Exception as e:
                log(f"Approach failed: {e}")
                continue
        
        if not success:
            log(f"All approaches failed for task: {task_name}")
            
        return success
    
    @staticmethod
    def _try_current_user(task_name: str, script_path: Path, windows_schedule: Dict, user: str, server_ip: str) -> bool:
        """Try creating task with current user context"""
        # Get current user - try multiple methods
        current_user = os.environ.get('USERNAME') or getpass.getuser()
        
        schtasks_cmd = [
            "schtasks", "/create",
            "/tn", task_name,
            "/tr", f'"{script_path}"',
            "/sc", windows_schedule["type"],
            *windows_schedule["params"],
            "/ru", current_user,
            "/f"
        ]
        
        full_cmd = " ".join(f'"{x}"' if " " in str(x) and not str(x).startswith("/") else str(x) for x in schtasks_cmd)
        log(f"Trying with current user '{current_user}': {full_cmd}")
        CommandExecuter.run_cmd(full_cmd, server_ip, user)
        return True
    
    @staticmethod
    def _try_administrator_user(task_name: str, script_path: Path, windows_schedule: Dict, user: str, server_ip: str) -> bool:
        """Try creating task with Administrator user"""
        schtasks_cmd = [
            "schtasks", "/create",
            "/tn", task_name,
            "/tr", f'"{script_path}"',
            "/sc", windows_schedule["type"],
            *windows_schedule["params"],
            "/ru", "Administrator",
            "/f"
        ]
        
        full_cmd = " ".join(f'"{x}"' if " " in str(x) and not str(x).startswith("/") else str(x) for x in schtasks_cmd)
        CommandExecuter.run_cmd(full_cmd, server_ip, user)
        return True
    
    @staticmethod
    def _try_system_user_with_password(task_name: str, script_path: Path, windows_schedule: Dict, user: str, server_ip: str) -> bool:
        """Try creating task with SYSTEM user and empty password"""
        schtasks_cmd = [
            "schtasks", "/create",
            "/tn", task_name,
            "/tr", f'"{script_path}"',
            "/sc", windows_schedule["type"],
            *windows_schedule["params"],
            "/ru", "SYSTEM",
            "/f"
        ]
        
        full_cmd = " ".join(f'"{x}"' if " " in str(x) and not str(x).startswith("/") else str(x) for x in schtasks_cmd)
        CommandExecuter.run_cmd(full_cmd, server_ip, user)
        return True

    @staticmethod
    def remove_scheduled_task(
        project: str,
        env: str,
        service_name: str,
        server_ip: str = "localhost",
        user: str = "Administrator"
    ) -> bool:
        """Remove Windows scheduled task"""
        task_name = f"{project}_{env}_{service_name}"
        
        try:
            cmd = f'schtasks /delete /tn "{task_name}" /f'
            CommandExecuter.run_cmd(cmd, server_ip, user)
            log(f"Removed Windows scheduled task: {task_name}")
            return True
        except Exception as e:
            log(f"Failed to remove Windows scheduled task {task_name}: {e}")
            return False
    
    @staticmethod
    def list_managed_tasks(
        project: str,
        env: str,
        server_ip: str = "localhost", 
        user: str = "Administrator"
    ) -> List[str]:
        """List managed Windows scheduled tasks"""
        try:
            cmd = 'schtasks /query /fo LIST /v'
            result = CommandExecuter.run_cmd(cmd, server_ip, user)
            output = result.stdout if hasattr(result, 'stdout') else str(result)
            
            # Parse tasks that match our naming pattern
            managed_tasks = []
            task_prefix = f"{project}_{env}_"
            
            for line in output.split('\n'):
                if 'TaskName:' in line and task_prefix in line:
                    task_name = line.split('TaskName:')[1].strip()
                    managed_tasks.append(task_name)
            
            return managed_tasks
            
        except Exception as e:
            log(f"Failed to list Windows scheduled tasks: {e}")
            return []
    
    @staticmethod
    def _convert_cron_to_windows(cron_schedule: str) -> Optional[Dict[str, Any]]:
        """Convert cron schedule to Windows Task Scheduler format"""
        parts = cron_schedule.strip().split()

        # Handle 6-field crons by stripping seconds
        if len(parts) == 6:
            log(f"Warning: Windows Task Scheduler does not support seconds field. "
                f"Converted '{cron_schedule}' to minute-level schedule.")
            parts = parts[1:]
            cron_schedule = " ".join(parts)

        if len(parts) != 5:
            log(f"Unsupported cron format for Windows: {cron_schedule}")
            return None

        minute, hour, day, month, dayofweek = parts

        # Case: every minute
        if minute == "*" and hour == "*":
            return {"type": "MINUTE", "params": ["/mo", "1"]}

        # Case: every N minutes
        if minute.startswith("*/") and hour == "*":
            interval = minute[2:]
            if interval.isdigit():
                return {"type": "MINUTE", "params": ["/mo", interval]}

        # Case: hourly at specific minute
        if hour == "*" and minute.isdigit():
            return {"type": "HOURLY", "params": ["/mo", "1", "/st", f"00:{minute.zfill(2)}"]}

        # Case: daily at specific hour/minute
        if minute.isdigit() and hour.isdigit():
            return {"type": "DAILY", "params": ["/st", f"{hour.zfill(2)}:{minute.zfill(2)}"]}

        # Midnight daily
        if minute == "0" and hour == "0":
            return {"type": "DAILY", "params": ["/st", "00:00"]}

        # Default fallback
        log(f"Complex cron schedule '{cron_schedule}' converted to basic daily task at 02:00")
        return {"type": "DAILY", "params": ["/st", "02:00"]}

    @staticmethod
    def _generate_docker_command(
        project: str,
        env: str, 
        service_name: str,
        service_config: Dict[str, Any],
        docker_hub_user: str,
        version: str
    ) -> str:
        """Generate Docker command for Windows scheduled task"""
        
        # Get image name
        if service_config.get("image"):
            image = service_config["image"]
        else:
            image = ResourceResolver.get_image_name(
                docker_hub_user, project, env, service_name, version
            )
        
        # Build Docker command
        container_name = f"{project}_{env}_{service_name}_%RANDOM%"
        network_name = ResourceResolver.get_network_name(project, env)
        
        docker_parts = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--network", network_name
        ]
        
        # Add volumes (directories auto-created)
        volumes = ResourceResolver.generate_all_volume_mounts(
            project, env, service_name,
            server_ip="localhost",  # Windows tasks run locally
            use_docker_volumes=True,
            user="root"
        )
        for volume in volumes:
            docker_parts.extend(["-v", volume])
        
        # Add environment variables
        env_vars = service_config.get("env_vars", {})
        for key, value in env_vars.items():
            docker_parts.extend(["-e", f"{key}={value}"])
        
        docker_parts.append(image)
        
        # Add custom command if specified
        command = service_config.get("command")
        if command:
            if isinstance(command, list):
                docker_parts.extend(command)
            else:
                docker_parts.append(command)
        
        # Join and escape for Windows
        return " ".join(f'"{part}"' if " " in str(part) else str(part) for part in docker_parts)


class EnhancedCronManager:
    """Enhanced cron manager with cross-platform support"""
    
    @staticmethod
    def install_scheduled_service(
        project: str,
        env: str,
        service_name: str,
        service_config: Dict[str, Any],
        docker_hub_user: str,
        version: str = "latest",
        server_ip: str = "localhost",
        user: str = "root"
    ) -> bool:
        """Install scheduled service with platform detection"""
        
        # Detect platform and scheduler
        platform, scheduler = PlatformScheduler.detect_platform_and_scheduler(server_ip, user)
        
        log(f"Target platform: {platform}, Scheduler: {scheduler}")
        
        if scheduler == "none":
            return EnhancedCronManager._handle_no_scheduler(
                platform, project, env, service_name, service_config, server_ip, user
            )
        elif scheduler == "cron":            
            return CronManager.install_cron_job(
                project, env, service_name, service_config,
                docker_hub_user, version, server_ip, user
            )
        elif scheduler == "schtasks":
            # Convert user context for Windows
            windows_user = EnhancedCronManager._get_windows_user_context(user, service_config)
            return WindowsTaskScheduler.create_scheduled_task(
                project, env, service_name, service_config,
                docker_hub_user, version, server_ip, windows_user
            )
        else:
            log(f"Unsupported scheduler: {scheduler}")
            return False
    
    @staticmethod
    def _get_windows_user_context(user: str, service_config: Dict[str, Any]) -> str:
        """Convert Unix user to appropriate Windows user context"""
        
        # Check if user is explicitly set in service config
        if "task_user" in service_config:
            return service_config["task_user"]
        
        # Map common Unix users to Windows equivalents
        user_mapping = {
            "root": "Administrator",
            "admin": "Administrator", 
            "administrator": "Administrator"
        }
        
        mapped_user = user_mapping.get(user.lower())
        if mapped_user:
            log(f"Mapped Unix user '{user}' to Windows user '{mapped_user}'")
            return mapped_user
        
        # If it looks like a Windows user format, use as-is
        if "\\" in user or user.upper() in ["SYSTEM", "ADMINISTRATOR"]:
            return user
            
        # Default to Administrator for compatibility
        log(f"Using Administrator context for unrecognized user '{user}'")
        return "Administrator"

    @staticmethod
    def _handle_no_scheduler(
        platform: str, 
        project: str, 
        env: str, 
        service_name: str, 
        service_config: Dict[str, Any], 
        server_ip: str, 
        user: str
    ) -> bool:
        """Handle case where no scheduler is available"""
        log(f"No scheduler available on {platform}. Manual setup required.")
        log(f"To run {service_name} manually:")
        
        # Generate the command that would have been scheduled
        docker_cmd = WindowsTaskScheduler._generate_docker_command(
            project, env, service_name, service_config, "dockerhub_user", "latest"
        ) if platform == "windows" else "docker run command here"
        
        log(f"Command: {docker_cmd}")
        log(f"Schedule: {service_config.get('schedule', 'Not specified')}")
        
        return False  # Return False since we couldn't actually schedule it

    @staticmethod
    def remove_scheduled_service(
        project: str,
        env: str,
        service_name: str,
        server_ip: str = "localhost",
        user: str = "root"
    ) -> bool:
        """Remove scheduled service with platform detection"""
        
        platform, scheduler = PlatformScheduler.detect_platform_and_scheduler(server_ip, user)
        
        if scheduler == "cron":            
            return CronManager.remove_cron_job(project, env, service_name, server_ip, user)
        elif scheduler == "schtasks":
            return WindowsTaskScheduler.remove_scheduled_task(
                project, env, service_name, server_ip, user or "Administrator"
            )
        else:
            log(f"Cannot remove - no supported scheduler found on {platform}")
            return False
    
    @staticmethod
    def list_scheduled_services(
        project: str,
        env: str,
        server_ip: str = "localhost",
        user: str = "root"
    ) -> List[str]:
        """List scheduled services with platform detection"""
        
        platform, scheduler = PlatformScheduler.detect_platform_and_scheduler(server_ip, user)
        
        if scheduler == "cron":            
            return CronManager.list_managed_cron_jobs(project, env, server_ip, user)
        elif scheduler == "schtasks":
            return WindowsTaskScheduler.list_managed_tasks(project, env, server_ip, user or "Administrator")
        else:
            return []