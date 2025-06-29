import os
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import logging

from enums import Envs, ServiceTypes


@dataclass
class ScheduledJob:
    """Configuration for a scheduled job"""
    name: str
    schedule: str  # Cron expression
    script_path: str  # Path to Python script/module
    args: List[str] = None  # Command line arguments
    env_vars: Dict[str, str] = None  # Additional environment variables
    working_dir: str = "/app"  # Working directory
    enabled: bool = True
    description: str = ""
    
    def __post_init__(self):
        if self.args is None:
            self.args = []
        if self.env_vars is None:
            self.env_vars = {}


class JobScheduler:
    """
    Centralized job scheduler that manages cron jobs dynamically.
    
    Features:
    - Add/remove jobs at runtime
    - Load jobs from configuration files
    - Integration with existing codebase
    - Centralized logging and monitoring
    - Support for complex Python projects
    
    Examples:
        ```python
        scheduler = JobScheduler()
        
        # Add a backup job
        backup_job = ScheduledJob(
            name="backup_maindb",
            schedule="0 2 * * *",
            script_path="jobs/backup_job.py",
            args=["ecommerce", "prod", "maindb"],
            description="Daily backup of main database"
        )
        scheduler.add_job(backup_job)
        
        # Load jobs from config file
        scheduler.load_jobs_from_config("config/jobs.yml")
        
        # Start the scheduler
        scheduler.start()
        ```
    """
    
    def __init__(self, 
                 config_dir: str = "/app/config",
                 log_dir: str = "/var/log/jobs",
                 scripts_dir: str = "/app/jobs"):
        """
        Initialize the job scheduler.
        
        Args:
            config_dir: Directory containing job configuration files
            log_dir: Directory for job logs
            scripts_dir: Directory containing job scripts
        """
        self.config_dir = Path(config_dir)
        self.log_dir = Path(log_dir)
        self.scripts_dir = Path(scripts_dir)
        self.jobs: Dict[str, ScheduledJob] = {}
        
        # Ensure directories exist
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging
        self._setup_logging()
        
        print(f"🕐 JobScheduler initialized:")
        print(f"   Config dir: {self.config_dir}")
        print(f"   Log dir: {self.log_dir}")
        print(f"   Scripts dir: {self.scripts_dir}")
    
    def _setup_logging(self):
        """Setup logging for the scheduler"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.log_dir / 'scheduler.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger('JobScheduler')
    
    def add_job(self, job: ScheduledJob) -> bool:
        """
        Add a job to the scheduler.
        
        Args:
            job: ScheduledJob instance
            
        Returns:
            bool: True if job added successfully
        """
        try:
            # Validate job
            if not self._validate_job(job):
                return False
            
            # Add to internal registry
            self.jobs[job.name] = job
            
            # Update crontab
            self._update_crontab()
            
            self.logger.info(f"✅ Added job: {job.name} ({job.schedule})")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Failed to add job {job.name}: {e}")
            return False
    
    def remove_job(self, job_name: str) -> bool:
        """
        Remove a job from the scheduler.
        
        Args:
            job_name: Name of the job to remove
            
        Returns:
            bool: True if job removed successfully
        """
        try:
            if job_name not in self.jobs:
                self.logger.warning(f"⚠️ Job {job_name} not found")
                return False
            
            # Remove from internal registry
            del self.jobs[job_name]
            
            # Update crontab
            self._update_crontab()
            
            self.logger.info(f"✅ Removed job: {job_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Failed to remove job {job_name}: {e}")
            return False
    
    def enable_job(self, job_name: str) -> bool:
        """Enable a job"""
        if job_name in self.jobs:
            self.jobs[job_name].enabled = True
            self._update_crontab()
            self.logger.info(f"✅ Enabled job: {job_name}")
            return True
        return False
    
    def disable_job(self, job_name: str) -> bool:
        """Disable a job"""
        if job_name in self.jobs:
            self.jobs[job_name].enabled = False
            self._update_crontab()
            self.logger.info(f"⏸️ Disabled job: {job_name}")
            return True
        return False
    
    def list_jobs(self) -> List[Dict[str, Any]]:
        """
        List all jobs with their details.
        
        Returns:
            List of job dictionaries
        """
        job_list = []
        for job in self.jobs.values():
            job_dict = asdict(job)
            job_dict['status'] = 'enabled' if job.enabled else 'disabled'
            job_list.append(job_dict)
        
        return sorted(job_list, key=lambda x: x['name'])
    
    def run_job_now(self, job_name: str) -> bool:
        """
        Run a job immediately (outside of schedule).
        
        Args:
            job_name: Name of the job to run
            
        Returns:
            bool: True if job started successfully
        """
        if job_name not in self.jobs:
            self.logger.error(f"❌ Job {job_name} not found")
            return False
        
        job = self.jobs[job_name]
        
        try:
            self.logger.info(f"🚀 Running job manually: {job_name}")
            result = self._execute_job(job)
            
            if result:
                self.logger.info(f"✅ Manual job execution successful: {job_name}")
            else:
                self.logger.error(f"❌ Manual job execution failed: {job_name}")
            
            return result
            
        except Exception as e:
            self.logger.error(f"❌ Error running job {job_name}: {e}")
            return False
    
    def load_jobs_from_config(self, config_file: str = "jobs.yml") -> int:
        """
        Load jobs from a configuration file.
        
        Args:
            config_file: Path to configuration file (YAML or JSON)
            
        Returns:
            int: Number of jobs loaded
        """
        config_path = self.config_dir / config_file
        
        if not config_path.exists():
            self.logger.warning(f"⚠️ Config file not found: {config_path}")
            return 0
        
        try:
            if config_file.endswith('.yml') or config_file.endswith('.yaml'):
                import yaml
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
            else:
                with open(config_path, 'r') as f:
                    config = json.load(f)
            
            jobs_loaded = 0
            for job_config in config.get('jobs', []):
                job = ScheduledJob(**job_config)
                if self.add_job(job):
                    jobs_loaded += 1
            
            self.logger.info(f"📋 Loaded {jobs_loaded} jobs from {config_file}")
            return jobs_loaded
            
        except Exception as e:
            self.logger.error(f"❌ Failed to load config {config_file}: {e}")
            return 0
    
    def save_jobs_to_config(self, config_file: str = "jobs.yml") -> bool:
        """
        Save current jobs to a configuration file.
        
        Args:
            config_file: Path to save configuration
            
        Returns:
            bool: True if saved successfully
        """
        config_path = self.config_dir / config_file
        
        try:
            config = {
                'jobs': [asdict(job) for job in self.jobs.values()],
                'last_updated': datetime.now().isoformat()
            }
            
            if config_file.endswith('.yml') or config_file.endswith('.yaml'):
                import yaml
                with open(config_path, 'w') as f:
                    yaml.safe_dump(config, f, indent=2)
            else:
                with open(config_path, 'w') as f:
                    json.dump(config, f, indent=2)
            
            self.logger.info(f"💾 Saved {len(self.jobs)} jobs to {config_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Failed to save config {config_file}: {e}")
            return False
    
    def start(self):
        """
        Start the scheduler service.
        This should be called as the main container process.
        """
        self.logger.info("🚀 Starting JobScheduler service...")
        
        # Ensure cron is running
        if not self._ensure_cron_running():
            raise RuntimeError("Failed to start cron service")
        
        # Load jobs from default config if it exists
        self.load_jobs_from_config()
        
        # Update crontab with current jobs
        self._update_crontab()
        
        self.logger.info(f"✅ JobScheduler started with {len(self.jobs)} jobs")
        self._print_job_summary()
        
        # Monitor logs (this keeps the container running)
        self._monitor_logs()
    
    def _validate_job(self, job: ScheduledJob) -> bool:
        """Validate job configuration"""
        # Check if script exists
        script_path = Path(job.working_dir) / job.script_path
        if not script_path.exists():
            self.logger.error(f"❌ Script not found: {script_path}")
            return False
        
        # Validate cron expression (basic check)
        cron_parts = job.schedule.split()
        if len(cron_parts) != 5:
            self.logger.error(f"❌ Invalid cron expression: {job.schedule}")
            return False
        
        return True
    
    def _update_crontab(self):
        """Update the system crontab with current jobs"""
        try:
            cron_lines = []
            
            for job in self.jobs.values():
                if not job.enabled:
                    continue
                
                # Build command
                cmd_parts = [
                    "cd", job.working_dir, "&&",
                    "python", job.script_path
                ] + job.args
                
                # Add environment variables
                env_prefix = ""
                if job.env_vars:
                    env_vars = " ".join(f"{k}={v}" for k, v in job.env_vars.items())
                    env_prefix = f"env {env_vars} "
                
                # Build full command with logging
                log_file = self.log_dir / f"{job.name}.log"
                full_cmd = f"{env_prefix}{' '.join(cmd_parts)} >> {log_file} 2>&1"
                
                # Create cron line
                cron_line = f"{job.schedule} {full_cmd}  # {job.name}: {job.description}"
                cron_lines.append(cron_line)
            
            # Write to temporary file
            with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.cron') as f:
                f.write("# JobScheduler managed crontab\n")
                f.write(f"# Generated: {datetime.now().isoformat()}\n")
                f.write("# Do not edit manually - use JobScheduler API\n\n")
                
                for line in cron_lines:
                    f.write(line + "\n")
                
                temp_path = f.name
            
            # Install crontab
            result = subprocess.run(['crontab', temp_path], 
                                  capture_output=True, text=True)
            
            if result.returncode == 0:
                self.logger.info(f"✅ Updated crontab with {len(cron_lines)} jobs")
            else:
                self.logger.error(f"❌ Failed to update crontab: {result.stderr}")
            
            # Cleanup temp file
            os.unlink(temp_path)
            
        except Exception as e:
            self.logger.error(f"❌ Error updating crontab: {e}")
    
    def _ensure_cron_running(self) -> bool:
        """Ensure cron daemon is running"""
        try:
            # Start cron service
            result = subprocess.run(['service', 'cron', 'start'], 
                                  capture_output=True, text=True)
            
            if result.returncode != 0:
                self.logger.error(f"❌ Failed to start cron: {result.stderr}")
                return False
            
            # Verify cron is running
            result = subprocess.run(['pgrep', 'cron'], 
                                  capture_output=True, text=True)
            
            if result.returncode == 0:
                self.logger.info("✅ Cron daemon is running")
                return True
            else:
                self.logger.error("❌ Cron daemon not running")
                return False
                
        except Exception as e:
            self.logger.error(f"❌ Error checking cron status: {e}")
            return False
    
    def _execute_job(self, job: ScheduledJob) -> bool:
        """Execute a job directly (for manual runs)"""
        try:
            # Build command
            cmd = ['python', job.script_path] + job.args
            
            # Set environment
            env = os.environ.copy()
            env.update(job.env_vars)
            
            # Execute
            result = subprocess.run(
                cmd,
                cwd=job.working_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=3600  # 1 hour timeout
            )
            
            # Log output
            log_file = self.log_dir / f"{job.name}_manual.log"
            with open(log_file, 'a') as f:
                f.write(f"\n=== Manual execution at {datetime.now()} ===\n")
                f.write(f"Command: {' '.join(cmd)}\n")
                f.write(f"Exit code: {result.returncode}\n")
                f.write(f"STDOUT:\n{result.stdout}\n")
                f.write(f"STDERR:\n{result.stderr}\n")
            
            return result.returncode == 0
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"❌ Job {job.name} timed out")
            return False
        except Exception as e:
            self.logger.error(f"❌ Error executing job {job.name}: {e}")
            return False
    
    def _print_job_summary(self):
        """Print summary of loaded jobs"""
        if not self.jobs:
            print("📋 No jobs configured")
            return
        
        print(f"📋 Loaded Jobs ({len(self.jobs)} total):")
        print("-" * 80)
        
        for job in sorted(self.jobs.values(), key=lambda x: x.name):
            status = "✅" if job.enabled else "⏸️"
            print(f"  {status} {job.name:<20} {job.schedule:<15} {job.script_path}")
            if job.description:
                print(f"     📝 {job.description}")
        
        print("-" * 80)
    
    def _monitor_logs(self):
        """Monitor log files (keeps container running)"""
        log_files = ['/var/log/cron.log', str(self.log_dir / 'scheduler.log')]
        
        # Ensure log files exist
        for log_file in log_files:
            Path(log_file).touch()
        
        # Tail log files
        try:
            cmd = ['tail', '-f'] + log_files
            subprocess.run(cmd)
        except KeyboardInterrupt:
            self.logger.info("🛑 JobScheduler shutting down...")
        except Exception as e:
            self.logger.error(f"❌ Error monitoring logs: {e}")


# Job creation helpers for your existing codebase
class JobTemplates:
    """Templates for common job types using your existing codebase"""
    
    @staticmethod
    def backup_job(project_name: str, env: Envs, service_name: str, 
                   schedule: str = "0 2 * * *") -> ScheduledJob:
        """Create a backup job using your PostgreSQLBackupManager"""
        return ScheduledJob(
            name=f"backup_{project_name}_{env.value}_{service_name}",
            schedule=schedule,
            script_path="jobs/backup_job.py",
            args=[project_name, env.value, service_name],
            description=f"Backup {service_name} database for {project_name}/{env.value}"
        )
    
    @staticmethod
    def cleanup_job(project_name: str, env: Envs, 
                    schedule: str = "0 4 * * 0") -> ScheduledJob:
        """Create a cleanup job using your ContainerManager"""
        return ScheduledJob(
            name=f"cleanup_{project_name}_{env.value}",
            schedule=schedule,
            script_path="jobs/cleanup_job.py",
            args=[project_name, env.value],
            description=f"Weekly cleanup for {project_name}/{env.value}"
        )
    
    @staticmethod
    def health_check_job(project_name: str, env: Envs, services: List[str],
                        schedule: str = "*/10 * * * *") -> ScheduledJob:
        """Create a health check job using your ServiceLocator"""
        return ScheduledJob(
            name=f"health_{project_name}_{env.value}",
            schedule=schedule,
            script_path="jobs/health_check_job.py",
            args=[project_name, env.value] + services,
            description=f"Health check for {project_name}/{env.value} services"
        )


# CLI interface for job management
def scheduler_cli():
    """Command-line interface for the job scheduler"""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python scheduler.py <command> [options]")
        print("Commands:")
        print("  start                    - Start the scheduler service")
        print("  add <name> <schedule> <script> [args...]  - Add a job")
        print("  remove <name>            - Remove a job")
        print("  list                     - List all jobs")
        print("  enable <name>            - Enable a job")
        print("  disable <name>           - Disable a job")
        print("  run <name>               - Run a job manually")
        print("  status                   - Show scheduler status")
        return
    
    command = sys.argv[1]
    scheduler = JobScheduler()
    
    if command == "start":
        scheduler.start()
    
    elif command == "add":
        if len(sys.argv) < 5:
            print("Usage: add <name> <schedule> <script> [args...]")
            return
        
        name = sys.argv[2]
        schedule = sys.argv[3]
        script = sys.argv[4]
        args = sys.argv[5:] if len(sys.argv) > 5 else []
        
        job = ScheduledJob(name=name, schedule=schedule, script_path=script, args=args)
        if scheduler.add_job(job):
            print(f"✅ Added job: {name}")
        else:
            print(f"❌ Failed to add job: {name}")
    
    elif command == "remove":
        if len(sys.argv) < 3:
            print("Usage: remove <name>")
            return
        
        name = sys.argv[2]
        if scheduler.remove_job(name):
            print(f"✅ Removed job: {name}")
        else:
            print(f"❌ Failed to remove job: {name}")
    
    elif command == "list":
        jobs = scheduler.list_jobs()
        if jobs:
            print("📋 Scheduled Jobs:")
            print("-" * 80)
            for job in jobs:
                status = "✅" if job['enabled'] else "⏸️"
                print(f"  {status} {job['name']:<20} {job['schedule']:<15} {job['script_path']}")
        else:
            print("📋 No jobs configured")
    
    elif command == "enable":
        if len(sys.argv) < 3:
            print("Usage: enable <name>")
            return
        
        name = sys.argv[2]
        if scheduler.enable_job(name):
            print(f"✅ Enabled job: {name}")
        else:
            print(f"❌ Job not found: {name}")
    
    elif command == "disable":
        if len(sys.argv) < 3:
            print("Usage: disable <name>")
            return
        
        name = sys.argv[2]
        if scheduler.disable_job(name):
            print(f"⏸️ Disabled job: {name}")
        else:
            print(f"❌ Job not found: {name}")
    
    elif command == "run":
        if len(sys.argv) < 3:
            print("Usage: run <name>")
            return
        
        name = sys.argv[2]
        if scheduler.run_job_now(name):
            print(f"✅ Job executed: {name}")
        else:
            print(f"❌ Job execution failed: {name}")
    
    elif command == "status":
        print("🕐 JobScheduler Status:")
        print(f"   Jobs configured: {len(scheduler.jobs)}")
        
        # Check cron status
        try:
            result = subprocess.run(['service', 'cron', 'status'], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                print("   Cron daemon: ✅ Running")
            else:
                print("   Cron daemon: ❌ Not running")
        except:
            print("   Cron daemon: ❓ Unknown")
    
    else:
        print(f"❌ Unknown command: {command}")


if __name__ == "__main__":
    scheduler_cli()