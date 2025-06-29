from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class ServiceConfig:
    """
    Configuration object for customizing container generation.
    
    Allows specification of additional packages, setup commands, environment variables,
    and custom startup commands for any service type.
    
    Examples:
        ```python
        # Image processing API
        image_config = ServiceConfig(
            packages=["imagemagick", "libwebp-dev", "ffmpeg"],
            setup_commands=["pip install pillow opencv-python"],
            environment_vars={"MAX_IMAGE_SIZE": "50MB"}
        )
        
        # Backup worker with cron
        backup_config = ServiceConfig(
            packages=["cron", "postgresql-client", "jq"],
            setup_commands=["python backup_manager.py testlocal dev cron-install daily"],
            start_command="sh -c 'cron && tail -f /var/log/cron.log'"
        )
        
        # Standard service (no customization)
        standard_config = ServiceConfig()  # Uses all defaults
        ```
    """
    
    packages: List[str] = field(default_factory=list)
    """Additional system packages to install via apt-get"""
    
    setup_commands: List[str] = field(default_factory=list)
    """Commands to run during container build (after package installation)"""
    
    start_command: Optional[str] = None
    """Custom startup command (overrides default CMD for service type)"""
    
    environment_vars: Dict[str, str] = field(default_factory=dict)
    """Environment variables to set in the container"""
    
    user: Optional[str] = None
    """User to run the service as (overrides default user handling)"""
    
    working_dir: Optional[str] = None
    """Custom working directory (overrides default /app)"""
    
    def __post_init__(self):
        """Validate configuration after initialization"""
        # Validate packages
        if not isinstance(self.packages, list):
            raise ValueError("packages must be a list of strings")
        
        for pkg in self.packages:
            if not isinstance(pkg, str) or not pkg.strip():
                raise ValueError(f"Invalid package name: {pkg}")
        
        # Validate setup commands
        if not isinstance(self.setup_commands, list):
            raise ValueError("setup_commands must be a list of strings")
        
        for cmd in self.setup_commands:
            if cmd and (not isinstance(cmd, str) or not cmd.strip()):
                raise ValueError(f"Invalid setup command: {cmd}")
        
        # Validate start command
        if self.start_command is not None and (not isinstance(self.start_command, str) or not self.start_command.strip()):
            raise ValueError("start_command must be a non-empty string")
        
        # Validate environment variables
        if not isinstance(self.environment_vars, dict):
            raise ValueError("environment_vars must be a dictionary")
        
        for key, value in self.environment_vars.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(f"Environment variable {key}={value} must be string=string")
        
        # Validate user
        if self.user is not None and (not isinstance(self.user, str) or not self.user.strip()):
            raise ValueError("user must be a non-empty string")
        
        # Validate working directory
        if self.working_dir is not None and (not isinstance(self.working_dir, str) or not self.working_dir.strip()):
            raise ValueError("working_dir must be a non-empty string")
    
    def has_customizations(self) -> bool:
        """Check if this config has any customizations (not all defaults)"""
        return bool(
            self.packages or 
            self.setup_commands or 
            self.start_command or 
            self.environment_vars or 
            self.user or 
            self.working_dir
        )
    
    def get_package_install_command(self) -> str:
        """Generate apt-get install command for packages"""
        if not self.packages:
            return ""
        
        packages_str = " ".join(self.packages)
        return f"RUN apt-get update && apt-get install -y {packages_str} && rm -rf /var/lib/apt/lists/*"
    
    def get_setup_commands(self) -> str:
        """Generate RUN commands for setup"""
        if not self.setup_commands:
            return ""
        
        # Filter out empty commands and strip whitespace
        valid_commands = []
        for cmd in self.setup_commands:
            cmd = cmd.strip()
            if cmd:
                # Check if this is a multi-line heredoc command
                if ('<<' in cmd and 'EOF' in cmd) or cmd.count('\n') > 0:
                    # This is a multi-line command, treat as single RUN
                    valid_commands.append(cmd)
                else:
                    # Regular single-line command
                    valid_commands.append(cmd)
        
        if not valid_commands:
            return ""
        
        return "\n".join(f"RUN {cmd}" for cmd in valid_commands)
    
    def get_environment_vars(self) -> str:
        """Generate ENV commands for environment variables"""
        if not self.environment_vars:
            return ""
        
        env_lines = [f"ENV {key}={value}" for key, value in self.environment_vars.items()]
        return "\n".join(env_lines)
    
    def get_user_command(self) -> str:
        """Generate USER command if user is specified"""
        if not self.user:
            return ""
        
        return f"USER {self.user}"
    
    def get_workdir_command(self) -> str:
        """Generate WORKDIR command if working_dir is specified"""
        if not self.working_dir:
            return ""
        
        return f"WORKDIR {self.working_dir}"


# Predefined configurations for common use cases
class CommonServiceConfigs:
    """Predefined ServiceConfig objects for common scenarios"""
    
    @staticmethod
    def image_processing() -> ServiceConfig:
        """Configuration for services that process images"""
        return ServiceConfig(
            packages=["imagemagick", "libwebp-dev", "ffmpeg", "libvips-dev"],
            setup_commands=[
                "pip install pillow opencv-python-headless",
                "pip install pyvips"
            ],
            environment_vars={
                "MAX_IMAGE_SIZE": "50MB",
                "VIPS_CONCURRENCY": "4"
            }
        )
    
    @staticmethod
    def document_processing() -> ServiceConfig:
        """Configuration for services that process documents"""
        return ServiceConfig(
            packages=["pandoc", "wkhtmltopdf", "libreoffice", "poppler-utils"],
            setup_commands=[
                "pip install pypandoc python-docx PyPDF2",
                "pip install reportlab"
            ],
            environment_vars={
                "PANDOC_VERSION": "2.19",
                "MAX_DOC_SIZE": "100MB"
            }
        )
    
    @staticmethod
    def geospatial() -> ServiceConfig:
        """Configuration for services that work with geospatial data"""
        return ServiceConfig(
            packages=["gdal-bin", "libgdal-dev", "libproj-dev", "libgeos-dev"],
            setup_commands=[
                "pip install GDAL==$(gdal-config --version) --global-option=build_ext --global-option='-I/usr/include/gdal'",
                "pip install shapely fiona geopandas"
            ],
            environment_vars={
                "GDAL_DATA": "/usr/share/gdal",
                "PROJ_LIB": "/usr/share/proj"
            }
        )
    

    @staticmethod
    def centralized_scheduler() -> ServiceConfig:
        """Configuration for centralized job scheduler"""
        return ServiceConfig(
            packages=["cron", "postgresql-client", "jq", "curl"],
            setup_commands=[
                # Create required directories
                "mkdir -p /var/log/jobs /app/jobs /app/config",
                "chmod 755 /var/log/jobs /app/jobs /app/config",
                
                # Install PyYAML for config file support
                "pip install PyYAML",
                
                # Create simple entrypoint script using echo (avoid heredoc issues)
                "echo '#!/bin/bash' > /scheduler_entrypoint.sh",
                "echo 'set -e' >> /scheduler_entrypoint.sh",
                "echo 'echo \"🕐 Starting Centralized Job Scheduler...\"' >> /scheduler_entrypoint.sh",
                "echo 'mkdir -p /var/log/jobs /app/jobs /app/config' >> /scheduler_entrypoint.sh",
                "echo 'touch /var/log/cron.log' >> /scheduler_entrypoint.sh",
                "echo 'chmod 666 /var/log/cron.log' >> /scheduler_entrypoint.sh",
                "echo 'cd /app' >> /scheduler_entrypoint.sh",
                "echo 'python scheduler.py start' >> /scheduler_entrypoint.sh",
                
                # Make script executable
                "chmod +x /scheduler_entrypoint.sh"
            ],
            start_command="/scheduler_entrypoint.sh",
            user="root"  # Required for cron management
        )

    @staticmethod
    def backup_worker(project_name: str, env_name: str, service_name: str, frequency: str = "daily") -> ServiceConfig:
        """Configuration for backup worker with cron"""
        
        # Determine cron schedule
        if frequency == "demo":
            cron_schedule = "*/1 * * * *"  # Every minute for demo
        elif frequency == "daily":
            cron_schedule = "0 2 * * *"    # 2 AM daily
        elif frequency == "weekly":
            cron_schedule = "0 2 * * 0"    # 2 AM Sunday
        else:
            cron_schedule = frequency      # Custom cron expression
        
        return ServiceConfig(
            packages=["cron", "postgresql-client", "jq"],
            setup_commands=[
                # Create directories with proper permissions
                "mkdir -p /var/log /app/backups",
                "chmod 755 /var/log /app/backups",
                
                # Create comprehensive backup script with full environment
                "cat > /backup_script.sh << 'EOF'",
                "#!/bin/bash",
                "set -e",
                "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "export PYTHONPATH=/app",
                "cd /app",
                "echo \"$(date): Starting backup process...\" | tee -a /var/log/backup.log",
                "echo \"Current directory: $(pwd)\" | tee -a /var/log/backup.log",
                "echo \"Python path: $(which python)\" | tee -a /var/log/backup.log",
                "",
                "# Run the backup",
                f"python -c \"",
                f"import sys; sys.path.insert(0, '/app'); ",
                f"from backup_manager import PostgreSQLBackupManager; ",
                f"from enums import Envs; ",
                f"try:",
                f"    mgr = PostgreSQLBackupManager('{project_name}', Envs.{env_name.upper()}, '{service_name}'); ",
                f"    result = mgr.create_backup(); ",
                f"    if result: print('Backup created:', result);",
                f"    mgr.cleanup_old_backups(7); ",
                f"    print('WORKER: Backup process completed successfully');",
                f"except Exception as e:",
                f"    print('WORKER: Backup failed:', str(e));",
                f"    import traceback; traceback.print_exc();",
                f"    exit(1)",
                f"\" 2>&1 | tee -a /var/log/backup.log",
                "",
                "echo \"$(date): Backup process finished\" | tee -a /var/log/backup.log",
                "EOF",
                
                # Make script executable
                "chmod +x /backup_script.sh",
                
                # Create enhanced entrypoint script
                "cat > /entrypoint.sh << 'EOF'",
                "#!/bin/bash",
                "set -e",
                "",
                "echo \"Starting backup worker...\"",
                "",
                "# Ensure directories exist",
                "mkdir -p /var/log /app/backups",
                "touch /var/log/cron.log /var/log/backup.log",
                "chmod 666 /var/log/cron.log /var/log/backup.log",
                "",
                "# Create cron job",
                f"echo '{cron_schedule} /backup_script.sh' > /tmp/backup_cron",
                "echo '# End of cron file' >> /tmp/backup_cron",
                "",
                "# Install cron job",
                "crontab /tmp/backup_cron",
                "",
                "# Start cron daemon",
                "service cron start",
                "",
                "# Verify cron is running",
                "sleep 2",
                "if ! pgrep cron > /dev/null; then",
                "    echo 'ERROR: Cron daemon failed to start'",
                "    exit 1",
                "fi",
                "",
                "echo 'Cron daemon started successfully'",
                "echo 'Installed cron jobs:'",
                "crontab -l",
                "",
                # For demo mode, run one backup immediately
                f"# Run initial backup for demo" + (" \necho 'Running initial backup for demo...' \n/backup_script.sh &" if frequency == "demo" else ""),
                "",
                "# Monitor logs",
                "echo 'Monitoring backup worker logs...'",
                "echo 'Cron schedule: " + cron_schedule + "'",
                "tail -f /var/log/cron.log /var/log/backup.log",
                "EOF",
                
                # Make entrypoint executable
                "chmod +x /entrypoint.sh"
            ],
            start_command="/entrypoint.sh",
            user="root"  # Required for cron
        )

    @staticmethod
    def email_worker() -> ServiceConfig:
        """Configuration for email worker"""
        return ServiceConfig(
            packages=["postfix", "mailutils"],
            setup_commands=[
                "pip install sendgrid python-email-validator",
                "pip install jinja2"  # For email templates
            ],
            environment_vars={
                "EMAIL_QUEUE_SIZE": "100",
                "EMAIL_RETRY_ATTEMPTS": "3"
            }
        )
    
    @staticmethod
    def monitoring_worker() -> ServiceConfig:
        """Configuration for monitoring/notification worker"""
        return ServiceConfig(
            packages=["curl", "jq", "bc"],  # bc for calculations
            setup_commands=[
                "pip install requests prometheus-client",
                "pip install slack-sdk discord-webhook"
            ],
            environment_vars={
                "MONITORING_INTERVAL": "60",
                "ALERT_THRESHOLD": "90"
            }
        )
    
    @staticmethod
    def machine_learning() -> ServiceConfig:
        """Configuration for ML/AI services"""
        return ServiceConfig(
            packages=["libgomp1", "libopenblas-dev", "liblapack-dev"],
            setup_commands=[
                "pip install numpy scipy scikit-learn",
                "pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu"
            ],
            environment_vars={
                "OMP_NUM_THREADS": "4",
                "OPENBLAS_NUM_THREADS": "4"
            }
        )