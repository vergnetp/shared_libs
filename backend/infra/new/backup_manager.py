import os
import datetime
import subprocess
from pathlib import Path
from typing import Optional, List
from secrets_manager import SecretsManager
from enums import Envs, ServiceTypes
from container_generator import ContainerGenerator
from service_locator import ServiceLocator

class PostgreSQLBackupManager:
    """
    Automated PostgreSQL backup system that integrates with your container setup.
    
    Features:
    - Automated pg_dump backups
    - Retention policy (keep last N backups)
    - Integration with your secrets management
    - Support for both local and remote storage
    - Restoration capabilities
    """
    
    def __init__(self, project_name: str, env: Envs, service_name: str='maindb', backup_dir: str = None):
        
        self.service_name = service_name
        self.project_name = project_name
        self.env = Envs.to_enum(env)
        
        # Get database connection details using static methods      
        self.db_name = ContainerGenerator.generate_identifier(project_name, env, "database")
        self.db_user = ContainerGenerator.generate_identifier(project_name, env, "user")
        self.container_name = ContainerGenerator.generate_container_name(project_name, env, self.service_name)
        
        (host, port) = ServiceLocator.get_host_port(project_name, env, ServiceTypes.POSTGRES, service_name)
      
        self.db_port = port
        
      
        self.db_host = host
        
        # Set backup directory
        self.backup_dir = Path(backup_dir) if backup_dir else Path.cwd() / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        
        print(f"🗄️ Backup manager initialized:")
        print(f"   Database: {self.db_name}")
        print(f"   User: {self.db_user}")
        print(f"   Host: {self.db_host}")
        print(f"   Port: {self.db_port}")
        print(f"   Container: {self.container_name}")
        print(f"   Backup dir: {self.backup_dir}")
    
    def create_backup(self, backup_name: str = None, 
                     compression: bool = True) -> Optional[str]:
        """
        Create a PostgreSQL backup using pg_dump.
        
        Args:
            backup_name: Custom backup name (default: timestamp-based)
            compression: Whether to compress the backup (saves ~70% space)
            
        Returns:
            str: Path to created backup file, or None if failed
        """
        try:
            # Generate backup filename
            if not backup_name:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_name = f"{self.project_name}_{self.env.value}_{timestamp}"
            
            extension = ".sql.gz" if compression else ".sql"
            backup_file = self.backup_dir / f"{backup_name}{extension}"
            
            print(f"📦 Creating backup: {backup_file}")
            print(f"🔄 Running pg_dump...")
            
            # Get password from mounted secrets file using static method
            import json
            secrets_file = SecretsManager.get_secrets_file()
            try:
                with open(secrets_file, 'r') as f:
                    secrets = json.load(f)
                postgres_password = secrets['postgres']
            except Exception as e:
                print(f"❌ Failed to read password from {secrets_file}: {e}")
                return None
            
            # Set up environment for pg_dump
            env = os.environ.copy()
            env['PGPASSWORD'] = postgres_password
            
            # Try different connection strategies
            hosts_to_try = [
                self.db_host,  # Container name first
                "localhost",   # Localhost fallback
                "127.0.0.1"    # IP fallback
            ]
            
            for host in hosts_to_try:
                print(f"🔗 Trying connection to: {host}:{self.db_port}")
                
                dump_cmd = [
                    "pg_dump",
                    "-h", host,
                    "-p", str(self.db_port),               
                    "-U", self.db_user,
                    "-d", self.db_name,
                    "--verbose",
                    f"--format={'custom' if compression else 'plain'}"
                ]
                
                # Test connection first
                test_cmd = [
                    "pg_isready",
                    "-h", host,
                    "-p", str(self.db_port),
                    "-U", self.db_user
                ]
                
                test_result = subprocess.run(test_cmd, env=env, capture_output=True, text=True, timeout=10)
                if test_result.returncode != 0:
                    print(f"   ❌ Connection test failed: {test_result.stderr}")
                    continue
                
                print(f"   ✅ Connection test successful")
                
                # Execute backup
                with open(backup_file, 'wb') as f:
                    result = subprocess.run(
                        dump_cmd,
                        stdout=f,
                        stderr=subprocess.PIPE,
                        env=env,
                        timeout=3600  # 1 hour timeout
                    )
                
                if result.returncode == 0:
                    file_size = backup_file.stat().st_size
                    print(f"✅ Backup created successfully!")
                    print(f"   File: {backup_file}")
                    print(f"   Size: {self._format_size(file_size)}")
                    return str(backup_file)
                else:
                    print(f"   ❌ Backup failed: {result.stderr.decode()}")
                    continue
            
            # All hosts failed
            print(f"❌ Backup failed on all connection attempts")
            print(f"💡 Make sure PostgreSQL container '{self.container_name}' is running and accessible")
            backup_file.unlink(missing_ok=True)
            return None
                
        except subprocess.TimeoutExpired:
            print("❌ Backup timed out (>1 hour)")
            return None
        except Exception as e:
            print(f"❌ Backup error: {e}")
            return None
    
    def restore_backup(self, backup_file: str, 
                      drop_existing: bool = False) -> bool:
        """
        Restore a PostgreSQL backup.
        
        Args:
            backup_file: Path to backup file
            drop_existing: Whether to drop existing database first (DANGEROUS!)
            
        Returns:
            bool: True if restoration successful
        """
        backup_path = Path(backup_file)
        if not backup_path.exists():
            print(f"❌ Backup file not found: {backup_file}")
            return False
        
        try:
            print(f"🔄 Restoring backup: {backup_file}")
            
            # Get password from mounted secrets file using static method
            import json
            secrets_file = SecretsManager.get_secrets_file()
            try:
                with open(secrets_file, 'r') as f:
                    secrets = json.load(f)
                postgres_password = secrets['postgres']
            except Exception as e:
                print(f"❌ Failed to read password from {secrets_file}: {e}")
                return False
            
            # Set up environment for PostgreSQL commands
            env = os.environ.copy()
            env['PGPASSWORD'] = postgres_password
            
            # Try different hosts
            hosts_to_try = [self.db_host, "localhost", "127.0.0.1"]
            
            for host in hosts_to_try:
                print(f"🔗 Trying restore to: {host}:{self.db_port}")
                
                # Drop existing database if requested
                if drop_existing:
                    print("⚠️ Dropping existing database...")
                    drop_cmd = [
                        "dropdb",
                        "-h", host,
                        "-p", str(self.db_port), 
                        "-U", self.db_user,
                        self.db_name
                    ]
                    
                    subprocess.run(drop_cmd, env=env, check=False)  # Ignore errors if DB doesn't exist
                    
                    # Recreate database
                    create_cmd = [
                        "createdb",
                        "-h", host,
                        "-p", str(self.db_port),
                        "-U", self.db_user,
                        self.db_name
                    ]
                    
                    result = subprocess.run(create_cmd, env=env)
                    if result.returncode != 0:
                        print(f"   ❌ Failed to recreate database on {host}")
                        continue
                
                # Determine restore command based on backup format
                is_compressed = backup_file.endswith('.gz') or backup_file.endswith('.dump')
                
                if is_compressed:
                    # Custom format restore
                    restore_cmd = [
                        "pg_restore",
                        "-h", host,
                        "-p", str(self.db_port),
                        "-U", self.db_user,
                        "-d", self.db_name,
                        "--verbose",
                        backup_file
                    ]
                else:
                    # Plain SQL restore
                    restore_cmd = [
                        "psql",
                        "-h", host,
                        "-p", str(self.db_port),
                        "-U", self.db_user,
                        "-d", self.db_name,
                        "-f", backup_file
                    ]
                
                result = subprocess.run(restore_cmd, env=env)
                
                if result.returncode == 0:
                    print(f"✅ Database restored successfully from {backup_file}")
                    return True
                else:
                    print(f"   ❌ Restore failed on {host}")
                    continue
            
            print(f"❌ Restore failed on all connection attempts")
            return False
                
        except Exception as e:
            print(f"❌ Restore error: {e}")
            return False

    def list_backups(self) -> List[dict]:
        """List all available backups with metadata."""
        backups = []
        
        for backup_file in self.backup_dir.glob(f"{self.project_name}_{self.env.value}_*.sql*"):
            stat = backup_file.stat()
            
            # Extract timestamp from filename
            try:
                filename = backup_file.stem.replace('.sql', '')
                timestamp_str = filename.split('_')[-2] + '_' + filename.split('_')[-1]
                timestamp = datetime.datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            except:
                timestamp = datetime.datetime.fromtimestamp(stat.st_mtime)
            
            backups.append({
                "file": str(backup_file),
                "name": backup_file.name,
                "size": stat.st_size,
                "size_formatted": self._format_size(stat.st_size),
                "created": timestamp,
                "age_days": (datetime.datetime.now() - timestamp).days
            })
        
        # Sort by creation time (newest first)
        backups.sort(key=lambda x: x["created"], reverse=True)
        return backups
    
    def cleanup_old_backups(self, keep_count: int = 7) -> int:
        """
        Remove old backups, keeping only the most recent ones.
        
        Args:
            keep_count: Number of backups to keep (default: 7)
            
        Returns:
            int: Number of backups removed
        """
        backups = self.list_backups()
        
        if len(backups) <= keep_count:
            print(f"📁 Only {len(backups)} backups found, keeping all")
            return 0
        
        to_remove = backups[keep_count:]
        removed_count = 0
        
        print(f"🧹 Removing {len(to_remove)} old backups (keeping {keep_count} most recent)")
        
        for backup in to_remove:
            try:
                Path(backup["file"]).unlink()
                print(f"   ✅ Removed: {backup['name']} ({backup['age_days']} days old)")
                removed_count += 1
            except Exception as e:
                print(f"   ❌ Failed to remove {backup['name']}: {e}")
        
        return removed_count
    
    def generate_cron_command(self, frequency: str = "daily") -> str:
        """
        Generate cron job command for automated backups.
        
        Args:
            frequency: "daily", "weekly", or custom cron expression
            
        Returns:
            str: Cron job command to manually add to crontab
        """
        script_path = Path(__file__).absolute()
        
        if frequency == "daily":
            cron_time = "0 2 * * *"  # 2 AM daily
        elif frequency == "weekly":
            cron_time = "0 2 * * 0"  # 2 AM every Sunday
        else:
            cron_time = frequency  # Custom cron expression
        
        cron_cmd = f"{cron_time} cd {Path.cwd()} && python -c \"from {Path(__file__).stem} import PostgreSQLBackupManager; from enums import Envs; mgr = PostgreSQLBackupManager('{self.project_name}', Envs.{self.env.name}); mgr.create_backup(); mgr.cleanup_old_backups()\""
        
        print(f"📅 Generated {frequency} backup cron command:")
        print(f"   {cron_cmd}")
        print()
        print(f"💡 To schedule this backup, manually add to your crontab:")
        print(f"   crontab -e")
        print(f"   # Add the line above")
        print()
        print(f"🔍 To verify cron job is added:")
        print(f"   crontab -l | grep backup")
        
        return cron_cmd

    def remove_cron_job(self) -> bool:
        """
        Remove the installed cron job for this project/environment.
        
        Returns:
            bool: True if cron job removed successfully
        """
        try:
            import tempfile
            
            # Get current crontab
            try:
                current_crontab = subprocess.run(
                    ["crontab", "-l"], 
                    capture_output=True, 
                    text=True,
                    check=True
                ).stdout
            except subprocess.CalledProcessError:
                print(f"📭 No existing crontab found")
                return True
            
            # Remove backup job lines
            backup_marker = f"# PostgreSQL backup for {self.project_name}_{self.env.value}"
            lines = current_crontab.split('\n')
            new_lines = []
            skip_next = False
            
            for line in lines:
                if backup_marker in line:
                    skip_next = True  # Skip the marker and the next line (cron command)
                    continue
                elif skip_next:
                    skip_next = False  # Skip the cron command line
                    continue
                else:
                    new_lines.append(line)
            
            # Write new crontab
            new_crontab = '\n'.join(new_lines)
            
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                f.write(new_crontab)
                temp_file = f.name
            
            # Install new crontab
            subprocess.run(["crontab", temp_file], check=True)
            
            # Cleanup temp file
            Path(temp_file).unlink()
            
            print(f"✅ Cron job removed successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Error removing cron job: {e}")
            return False

    def install_cron_job(self, frequency: str = "daily") -> bool:
        """
        Actually install the cron job (requires crontab command).
        
        Args:
            frequency: "daily", "weekly", or custom cron expression
            
        Returns:
            bool: True if cron job installed successfully
        """
        try:
            import tempfile
            
            cron_cmd = self.generate_cron_command(frequency)
            
            # Get current crontab
            try:
                current_crontab = subprocess.run(
                    ["crontab", "-l"], 
                    capture_output=True, 
                    text=True,
                    check=True
                ).stdout
            except subprocess.CalledProcessError:
                # No existing crontab
                current_crontab = ""
            
            # Check if backup job already exists
            backup_marker = f"# PostgreSQL backup for {self.project_name}_{self.env.value}"
            if backup_marker in current_crontab:
                print(f"⚠️ Backup cron job already exists for {self.project_name}_{self.env.value}")
                return False
            
            # Create new crontab content
            new_crontab = current_crontab + f"\n{backup_marker}\n{cron_cmd}\n"
            
            # Write to temporary file and install
            with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
                f.write(new_crontab)
                temp_file = f.name
            
            # Install new crontab
            result = subprocess.run(["crontab", temp_file], check=True)
            
            # Cleanup temp file
            Path(temp_file).unlink()
            
            print(f"✅ Cron job installed successfully!")
            print(f"🔍 Verify with: crontab -l")
            
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to install cron job: {e}")
            print(f"💡 Make sure 'crontab' command is available")
            return False
        except Exception as e:
            print(f"❌ Error installing cron job: {e}")
            return False
    
    def debug_environment(self):
        """Debug method to check environment and connections"""
        print(f"🔍 Environment Debug:")
        print(f"   Current directory: {os.getcwd()}")
        print(f"   Python executable: {sys.executable}")
        print(f"   Container name: {self.container_name}")
        print(f"   Database: {self.db_name}")
        print(f"   User: {self.db_user}")
        print(f"   Host: {self.db_host}")
        print(f"   Port: {self.db_port}")
        print(f"   Backup directory: {self.backup_dir}")
        print(f"   Directory exists: {self.backup_dir.exists()}")
        print(f"   Directory writable: {os.access(self.backup_dir, os.W_OK)}")

    def _is_container_running(self) -> bool:
        """Check if PostgreSQL container is running."""
        try:
            result = subprocess.run([
                "docker", "ps", "--filter", f"name={self.container_name}",
                "--format", "{{.Names}}"
            ], capture_output=True, text=True)
            
            return self.container_name in result.stdout
        except:
            return False
    
    def _format_size(self, size_bytes: int) -> str:
        """Format file size in human readable format."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"


# CLI interface for backup operations
def backup_cli():
    """Command-line interface for backup operations."""
    import sys
    
    if len(sys.argv) < 4:
        print("Usage: python backup_manager.py <project> <env> <action> [options]")
        print("Actions:")
        print("  backup [name]           - Create backup")
        print("  restore <file>          - Restore backup") 
        print("  list                    - List backups")
        print("  cleanup [keep_count]    - Remove old backups")
        print("  cron-generate [freq]    - Generate cron command (manual install)")
        print("  cron-install [freq]     - Actually install cron job")
        print("  cron-remove             - Remove installed cron job")
        print()
        print("Frequencies: daily, weekly, or custom cron expression (e.g., '0 3 * * *')")
        return
    
    project = sys.argv[1]
    env = Envs(sys.argv[2])
    action = sys.argv[3]
    
    mgr = PostgreSQLBackupManager(project, env)
    
    if action == "backup":
        backup_name = sys.argv[4] if len(sys.argv) > 4 else None
        result = mgr.create_backup(backup_name)
        if result:
            print(f"✅ Backup created: {result}")
        else:
            sys.exit(1)
    
    elif action == "restore":
        if len(sys.argv) < 5:
            print("❌ Backup file required for restore")
            sys.exit(1)
        
        backup_file = sys.argv[4]
        success = mgr.restore_backup(backup_file, drop_existing=True)
        sys.exit(0 if success else 1)
    
    elif action == "list":
        backups = mgr.list_backups()
        if backups:
            print(f"📁 Found {len(backups)} backups:")
            for backup in backups:
                print(f"   {backup['name']} - {backup['size_formatted']} - {backup['created']} ({backup['age_days']} days old)")
        else:
            print("📁 No backups found")
    
    elif action == "cleanup":
        keep_count = int(sys.argv[4]) if len(sys.argv) > 4 else 7
        removed = mgr.cleanup_old_backups(keep_count)
        print(f"🧹 Removed {removed} old backups")
    
    elif action == "cron-generate":
        frequency = sys.argv[4] if len(sys.argv) > 4 else "daily"
        mgr.generate_cron_command(frequency)
    
    elif action == "cron-install":
        frequency = sys.argv[4] if len(sys.argv) > 4 else "daily"
        success = mgr.install_cron_job(frequency)
        sys.exit(0 if success else 1)
    
    elif action == "cron-remove":
        success = mgr.remove_cron_job()
        sys.exit(0 if success else 1)
    
    else:
        print(f"❌ Unknown action: {action}")
        print("💡 Use 'cron-generate' to get the command, 'cron-install' to actually schedule it")
        sys.exit(1)


if __name__ == "__main__":
    backup_cli()