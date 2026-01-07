"""
Scheduler - Cron jobs and scheduled tasks.

Handles:
- System cron management
- Scheduled container tasks
- Backup scheduling
- Certificate renewal scheduling
"""

from __future__ import annotations
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from datetime import datetime
from enum import Enum

if TYPE_CHECKING:
    from ..context import DeploymentContext
    from ..ssh.client import SSHClient

from ..core.result import Result


class ScheduleFrequency(Enum):
    """Common schedule frequencies."""
    MINUTELY = "* * * * *"
    HOURLY = "0 * * * *"
    DAILY = "0 0 * * *"
    WEEKLY = "0 0 * * 0"
    MONTHLY = "0 0 1 * *"
    
    # Common patterns
    EVERY_5_MINUTES = "*/5 * * * *"
    EVERY_15_MINUTES = "*/15 * * * *"
    EVERY_30_MINUTES = "*/30 * * * *"
    EVERY_6_HOURS = "0 */6 * * *"
    EVERY_12_HOURS = "0 */12 * * *"
    
    # Specific times
    MIDNIGHT = "0 0 * * *"
    NOON = "0 12 * * *"
    WEEKDAYS_9AM = "0 9 * * 1-5"


@dataclass
class CronJob:
    """Cron job definition."""
    name: str
    schedule: str
    command: str
    user: str = "root"
    enabled: bool = True
    description: str = ""
    
    # Logging
    log_file: Optional[str] = None
    log_errors: bool = True
    
    # Environment
    environment: Dict[str, str] = field(default_factory=dict)
    
    def to_crontab_line(self) -> str:
        """Generate crontab line."""
        if not self.enabled:
            return f"# DISABLED: {self.name}\n# {self.schedule} {self.command}"
        
        # Build command with logging
        cmd = self.command
        
        if self.log_file:
            if self.log_errors:
                cmd = f"{cmd} >> {self.log_file} 2>&1"
            else:
                cmd = f"{cmd} >> {self.log_file}"
        
        # Add comment with name
        lines = [f"# {self.name}"]
        if self.description:
            lines.append(f"# {self.description}")
        
        # Add environment variables
        for key, value in self.environment.items():
            lines.append(f"{key}={value}")
        
        lines.append(f"{self.schedule} {cmd}")
        
        return "\n".join(lines)
    
    @classmethod
    def from_crontab_line(cls, line: str, name: str = "") -> Optional['CronJob']:
        """Parse crontab line."""
        line = line.strip()
        
        if not line or line.startswith("#"):
            return None
        
        parts = line.split(None, 5)
        if len(parts) < 6:
            return None
        
        schedule = " ".join(parts[:5])
        command = parts[5]
        
        return cls(
            name=name or f"job_{hash(line) % 10000}",
            schedule=schedule,
            command=command,
        )


@dataclass
class ScheduledTask:
    """Container-based scheduled task."""
    name: str
    schedule: str
    image: str
    command: Optional[str] = None
    
    # Container config
    environment: Dict[str, str] = field(default_factory=dict)
    volumes: Dict[str, str] = field(default_factory=dict)
    network: Optional[str] = None
    
    # Execution
    remove_after: bool = True
    timeout: int = 3600  # seconds
    
    def to_docker_command(self, namespace: str) -> str:
        """Generate docker run command."""
        container_name = f"{namespace}_{self.name}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        
        cmd = ["docker", "run"]
        
        if self.remove_after:
            cmd.append("--rm")
        
        cmd.extend(["--name", container_name])
        
        if self.network:
            cmd.extend(["--network", self.network])
        
        for key, value in self.environment.items():
            cmd.extend(["-e", f"{key}={value}"])
        
        for host_path, container_path in self.volumes.items():
            cmd.extend(["-v", f"{host_path}:{container_path}"])
        
        cmd.append(self.image)
        
        if self.command:
            cmd.append(self.command)
        
        return " ".join(cmd)


class Scheduler:
    """
    Scheduler for cron jobs and scheduled tasks.
    
    Usage:
        scheduler = Scheduler(ctx)
        
        # Add cron job
        scheduler.add_cron_job(CronJob(
            name="backup-db",
            schedule="0 2 * * *",  # 2 AM daily
            command="/scripts/backup.sh",
        ))
        
        # Add scheduled container task
        scheduler.add_scheduled_task(ScheduledTask(
            name="cleanup",
            schedule=ScheduleFrequency.DAILY.value,
            image="myapp/cleanup:latest",
        ))
        
        # List jobs
        jobs = scheduler.list_cron_jobs()
        
        # Remove job
        scheduler.remove_cron_job("backup-db")
    """
    
    CRON_DIR = "/etc/cron.d"
    CRON_PREFIX = "infra"
    
    def __init__(
        self, 
        ctx: 'DeploymentContext',
        ssh: Optional['SSHClient'] = None,
    ):
        self.ctx = ctx
        self.ssh = ssh
    
    def _exec(
        self, 
        cmd: str, 
        server: Optional[str] = None,
    ) -> tuple[int, str, str]:
        """Execute command."""
        if server and self.ssh:
            return self.ssh.exec(server, cmd)
        else:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=60
            )
            return result.returncode, result.stdout, result.stderr
    
    def _cron_file_path(self, name: str) -> str:
        """Get cron file path for a job."""
        safe_name = name.replace(" ", "_").replace("/", "_")
        return f"{self.CRON_DIR}/{self.CRON_PREFIX}_{self.ctx.namespace}_{safe_name}"
    
    # =========================================================================
    # Cron Jobs
    # =========================================================================
    
    def add_cron_job(
        self,
        job: CronJob,
        server: Optional[str] = None,
    ) -> Result:
        """
        Add a cron job.
        
        Args:
            job: CronJob definition
            server: Remote server
            
        Returns:
            Result
        """
        cron_file = self._cron_file_path(job.name)
        content = job.to_crontab_line()
        
        # Add trailing newline (required by cron)
        if not content.endswith("\n"):
            content += "\n"
        
        # Write cron file
        cmd = f"echo {repr(content)} > {cron_file} && chmod 644 {cron_file}"
        code, _, stderr = self._exec(cmd, server)
        
        if code == 0:
            self.ctx.log_info(f"Added cron job: {job.name}", schedule=job.schedule)
            return Result.ok(f"Cron job '{job.name}' added", path=cron_file)
        else:
            return Result.fail(stderr.strip())
    
    def remove_cron_job(
        self,
        name: str,
        server: Optional[str] = None,
    ) -> Result:
        """Remove a cron job."""
        cron_file = self._cron_file_path(name)
        
        code, _, stderr = self._exec(f"rm -f {cron_file}", server)
        
        if code == 0:
            self.ctx.log_info(f"Removed cron job: {name}")
            return Result.ok(f"Cron job '{name}' removed")
        else:
            return Result.fail(stderr.strip())
    
    def list_cron_jobs(
        self,
        server: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List cron jobs for this project."""
        pattern = f"{self.CRON_DIR}/{self.CRON_PREFIX}_{self.ctx.namespace}_*"
        
        code, stdout, _ = self._exec(f"cat {pattern} 2>/dev/null", server)
        
        jobs = []
        current_job = {"lines": []}
        
        for line in stdout.split("\n"):
            line = line.strip()
            if not line:
                continue
            
            if line.startswith("# ") and not current_job.get("name"):
                current_job["name"] = line[2:]
            elif not line.startswith("#") and "=" not in line[:20]:
                # This is the schedule line
                parts = line.split(None, 5)
                if len(parts) >= 6:
                    current_job["schedule"] = " ".join(parts[:5])
                    current_job["command"] = parts[5]
                    current_job["enabled"] = True
                    jobs.append(current_job)
                    current_job = {"lines": []}
        
        return jobs
    
    def enable_cron_job(
        self,
        name: str,
        server: Optional[str] = None,
    ) -> Result:
        """Enable a disabled cron job."""
        cron_file = self._cron_file_path(name)
        
        # Remove comment prefix from schedule line
        cmd = f"sed -i 's/^# \\([0-9*]\\)/\\1/' {cron_file}"
        code, _, stderr = self._exec(cmd, server)
        
        if code == 0:
            return Result.ok(f"Cron job '{name}' enabled")
        else:
            return Result.fail(stderr.strip())
    
    def disable_cron_job(
        self,
        name: str,
        server: Optional[str] = None,
    ) -> Result:
        """Disable a cron job (comment out)."""
        cron_file = self._cron_file_path(name)
        
        # Add comment prefix to schedule line
        cmd = f"sed -i 's/^\\([0-9*]\\)/# \\1/' {cron_file}"
        code, _, stderr = self._exec(cmd, server)
        
        if code == 0:
            return Result.ok(f"Cron job '{name}' disabled")
        else:
            return Result.fail(stderr.strip())
    
    # =========================================================================
    # Scheduled Container Tasks
    # =========================================================================
    
    def add_scheduled_task(
        self,
        task: ScheduledTask,
        server: Optional[str] = None,
    ) -> Result:
        """
        Add a scheduled container task.
        
        Creates a cron job that runs a container on schedule.
        
        Args:
            task: ScheduledTask definition
            server: Remote server
            
        Returns:
            Result
        """
        docker_cmd = task.to_docker_command(self.ctx.namespace)
        
        job = CronJob(
            name=f"task_{task.name}",
            schedule=task.schedule,
            command=docker_cmd,
            description=f"Container task: {task.image}",
            log_file=f"/var/log/{self.ctx.namespace}_{task.name}.log",
        )
        
        return self.add_cron_job(job, server)
    
    def remove_scheduled_task(
        self,
        name: str,
        server: Optional[str] = None,
    ) -> Result:
        """Remove a scheduled task."""
        return self.remove_cron_job(f"task_{name}", server)
    
    # =========================================================================
    # Common Scheduled Jobs
    # =========================================================================
    
    def schedule_backup(
        self,
        backup_command: str,
        schedule: str = ScheduleFrequency.DAILY.value,
        server: Optional[str] = None,
    ) -> Result:
        """
        Schedule a backup job.
        
        Args:
            backup_command: Backup command to run
            schedule: Cron schedule
            server: Remote server
        """
        job = CronJob(
            name="backup",
            schedule=schedule,
            command=backup_command,
            description="Automated backup",
            log_file=f"/var/log/{self.ctx.namespace}_backup.log",
        )
        return self.add_cron_job(job, server)
    
    def schedule_cert_renewal(
        self,
        server: Optional[str] = None,
    ) -> Result:
        """
        Schedule certificate renewal.
        
        Runs certbot renew twice daily (recommended by Let's Encrypt).
        """
        job = CronJob(
            name="cert-renewal",
            schedule="0 0,12 * * *",  # Twice daily
            command="certbot renew --quiet --post-hook 'systemctl reload nginx'",
            description="Let's Encrypt certificate renewal",
            log_file=f"/var/log/certbot-renew.log",
        )
        return self.add_cron_job(job, server)
    
    def schedule_cleanup(
        self,
        cleanup_command: str = "docker system prune -af --filter 'until=168h'",
        schedule: str = ScheduleFrequency.WEEKLY.value,
        server: Optional[str] = None,
    ) -> Result:
        """
        Schedule Docker cleanup.
        
        Removes unused images, containers, and volumes older than 7 days.
        """
        job = CronJob(
            name="docker-cleanup",
            schedule=schedule,
            command=cleanup_command,
            description="Docker cleanup",
            log_file=f"/var/log/{self.ctx.namespace}_cleanup.log",
        )
        return self.add_cron_job(job, server)
    
    def schedule_health_check(
        self,
        health_url: str,
        alert_command: Optional[str] = None,
        schedule: str = ScheduleFrequency.EVERY_5_MINUTES.value,
        server: Optional[str] = None,
    ) -> Result:
        """
        Schedule periodic health check.
        
        Args:
            health_url: URL to check
            alert_command: Command to run on failure
            schedule: Check frequency
        """
        check_cmd = f"curl -sf {health_url} > /dev/null"
        
        if alert_command:
            check_cmd = f"{check_cmd} || {alert_command}"
        
        job = CronJob(
            name="health-check",
            schedule=schedule,
            command=check_cmd,
            description=f"Health check for {health_url}",
        )
        return self.add_cron_job(job, server)
    
    def schedule_log_rotation(
        self,
        log_dir: str = "/var/log",
        max_size: str = "100M",
        keep_days: int = 30,
        server: Optional[str] = None,
    ) -> Result:
        """
        Schedule log rotation for project logs.
        
        Creates logrotate configuration.
        """
        logrotate_config = f"""{log_dir}/{self.ctx.namespace}_*.log {{
    daily
    rotate {keep_days}
    compress
    delaycompress
    missingok
    notifempty
    size {max_size}
    create 0644 root root
}}
"""
        config_path = f"/etc/logrotate.d/{self.CRON_PREFIX}_{self.ctx.namespace}"
        
        cmd = f"echo {repr(logrotate_config)} > {config_path}"
        code, _, stderr = self._exec(cmd, server)
        
        if code == 0:
            return Result.ok("Log rotation configured", path=config_path)
        else:
            return Result.fail(stderr.strip())
