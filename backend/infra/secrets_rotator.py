import secrets
import string
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from execute_docker import DockerExecuter
from resource_resolver import ResourceResolver
from logger import Logger

def log(msg):
    Logger.log(msg)

class SecretsRotator:
    """Handle rotation of secrets for deployed services"""
    
    def __init__(self, project: str, env: str):
        self.project = project
        self.env = env        
        self.safe_chars = string.ascii_letters + string.digits
    
    def _backup_secret(self, secret_path: Path) -> Path:
        """Create timestamped backup of existing secret"""
        if not secret_path.exists():
            return None
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = secret_path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        
        backup_path = backup_dir / f"{secret_path.name}.{timestamp}.bak"
        shutil.copy2(secret_path, backup_path)
        log(f"Backed up secret to: {backup_path}")
        return backup_path
    
    def _generate_password(self, length: int = 32) -> str:
        """Generate secure random password"""
        return ''.join(secrets.choice(self.safe_chars) for _ in range(length))
    
    def rotate_postgres_password(self, service_name: str = "postgres") -> bool:
        """Rotate PostgreSQL password"""
        try:
            secrets_dir = ResourceResolver.get_volume_host_path(
                self.project, self.env, service_name, "secrets", "localhost"
            )
            # Use ResourceResolver to get the correct secret filename
            secret_filename = ResourceResolver._get_secret_filename(service_name)
            password_file = Path(secrets_dir) / secret_filename
            
            # Backup existing password
            backup_path = self._backup_secret(password_file)
            
            # Generate new password
            new_password = self._generate_password()
            
            # Write new password
            Path(secrets_dir).mkdir(parents=True, exist_ok=True)
            password_file.write_text(new_password, encoding='utf-8')
            
            log(f"Generated new PostgreSQL password for {service_name}")
            
            # Restart container to pick up new password
            container_name = ResourceResolver.get_container_name(self.project, self.env, service_name)
            
            if DockerExecuter.container_exists(container_name):
                log(f"Restarting container {container_name} to apply new password...")
                DockerExecuter.stop_and_remove_container(container_name, ignore_if_not_exists=True)
                log(f"Container stopped. You need to redeploy to start with new password.")
            
            return True
            
        except Exception as e:
            log(f"Failed to rotate PostgreSQL password: {e}")
            return False
    
    def rotate_redis_password(self, service_name: str = "redis") -> bool:
        """Rotate Redis password"""
        try:
            secrets_dir = ResourceResolver.get_volume_host_path(
                self.project, self.env, service_name, "secrets", "localhost"
            )
            # Use ResourceResolver to get the correct secret filename
            secret_filename = ResourceResolver._get_secret_filename(service_name)
            password_file = Path(secrets_dir) / secret_filename
            
            # Backup existing password
            backup_path = self._backup_secret(password_file)
            
            # Generate new password
            new_password = self._generate_password()
            
            # Write new password
            Path(secrets_dir).mkdir(parents=True, exist_ok=True)
            password_file.write_text(new_password, encoding='utf-8')
            
            log(f"Generated new Redis password for {service_name}")
            
            # Restart container
            container_name = ResourceResolver.get_container_name(self.project, self.env, service_name)
            
            if DockerExecuter.container_exists(container_name):
                log(f"Restarting container {container_name} to apply new password...")
                DockerExecuter.stop_and_remove_container(container_name, ignore_if_not_exists=True)
                log(f"Container stopped. You need to redeploy to start with new password.")
            
            return True
            
        except Exception as e:
            log(f"Failed to rotate Redis password: {e}")
            return False
    
    def rotate_opensearch_password(self, service_name: str = "opensearch") -> bool:
        """Rotate OpenSearch admin password"""
        try:
            secrets_dir = ResourceResolver.get_volume_host_path(
                self.project, self.env, service_name, "secrets", "localhost"
            )
            # Use ResourceResolver to get the correct secret filename
            secret_filename = ResourceResolver._get_secret_filename(service_name)
            password_file = Path(secrets_dir) / secret_filename
            
            # Backup existing password
            backup_path = self._backup_secret(password_file)
            
            # Generate new password (OpenSearch has specific requirements)
            new_password = self._generate_password(length=16)  # Shorter for OpenSearch
            
            # Write new password
            Path(secrets_dir).mkdir(parents=True, exist_ok=True)
            password_file.write_text(new_password, encoding='utf-8')
            
            log(f"Generated new OpenSearch admin password for {service_name}")
            
            # Restart container
            container_name = ResourceResolver.get_container_name(self.project, self.env, service_name)
            
            if DockerExecuter.container_exists(container_name):
                log(f"Restarting container {container_name} to apply new password...")
                DockerExecuter.stop_and_remove_container(container_name, ignore_if_not_exists=True)
                log(f"Container stopped. You need to redeploy to start with new password.")
            
            return True
            
        except Exception as e:
            log(f"Failed to rotate OpenSearch password: {e}")
            return False
    
    def rotate_service_secret(self, service_name: str, secret_name: str, length: int = 32) -> bool:
        """Rotate a generic service secret"""
        try:
            secrets_dir = ResourceResolver.get_volume_host_path(
                self.project, self.env, service_name, "secrets", "localhost"
            )
            secret_file = Path(secrets_dir) / secret_name
            
            # Backup existing secret
            backup_path = self._backup_secret(secret_file)
            
            # Generate new secret
            new_secret = self._generate_password(length)
            
            # Write new secret
            Path(secrets_dir).mkdir(parents=True, exist_ok=True)
            secret_file.write_text(new_secret, encoding='utf-8')
            
            log(f"Generated new secret '{secret_name}' for {service_name}")
            
            return True
            
        except Exception as e:
            log(f"Failed to rotate secret '{secret_name}' for {service_name}: {e}")
            return False
    
    def rotate_all_secrets(self, services: Optional[List[str]] = None) -> Dict[str, bool]:
        """Rotate all secrets for specified services or all standard services"""
        if services is None:
            services = ["postgres", "redis", "opensearch"]
        
        results = {}
        
        for service in services:
            log(f"Rotating secrets for {service}...")
            
            if service == "postgres":
                results[service] = self.rotate_postgres_password()
            elif service == "redis":
                results[service] = self.rotate_redis_password()
            elif service == "opensearch":
                results[service] = self.rotate_opensearch_password()
            else:
                log(f"Unknown service type for rotation: {service}")
                results[service] = False
        
        # Summary
        successful = sum(1 for success in results.values() if success)
        total = len(results)
        log(f"Secret rotation complete: {successful}/{total} services successful")
        
        return results
    
    def list_secrets(self) -> Dict[str, List[str]]:
        """List all secrets for the project/environment"""
        secrets_base = ResourceResolver.get_volume_host_path(
            self.project, self.env, "", "secrets", "localhost"
        ).parent  # Go up one level to get the secrets base directory
        
        if not Path(secrets_base).exists():
            return {}
        
        secrets_map = {}
        
        for service_dir in Path(secrets_base).iterdir():
            if service_dir.is_dir():
                secret_files = []
                for secret_file in service_dir.iterdir():
                    if secret_file.is_file() and not secret_file.name.startswith('.'):
                        # Get file age
                        mtime = datetime.fromtimestamp(secret_file.stat().st_mtime)
                        age = datetime.now() - mtime
                        secret_files.append(f"{secret_file.name} (age: {age.days} days)")
                
                if secret_files:
                    secrets_map[service_dir.name] = secret_files
        
        return secrets_map
    
    def cleanup_old_backups(self, days_to_keep: int = 30):
        """Clean up secret backups older than specified days"""
        secrets_base = ResourceResolver.get_volume_host_path(
            self.project, self.env, "", "secrets", "localhost"
        ).parent  # Go up one level to get the secrets base directory
        
        if not Path(secrets_base).exists():
            return
        
        removed_count = 0
        cutoff_time = datetime.now().timestamp() - (days_to_keep * 24 * 60 * 60)
        
        for service_dir in Path(secrets_base).iterdir():
            if not service_dir.is_dir():
                continue
                
            backup_dir = service_dir / "backups"
            if not backup_dir.exists():
                continue
            
            for backup_file in backup_dir.iterdir():
                if backup_file.is_file() and backup_file.stat().st_mtime < cutoff_time:
                    backup_file.unlink()
                    removed_count += 1
                    log(f"Removed old backup: {backup_file}")
        
        log(f"Cleaned up {removed_count} old backup files")


def main():
    """CLI interface for secret rotation"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Rotate secrets for deployed services')
    parser.add_argument('project', help='Project name')
    parser.add_argument('env', help='Environment')
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Rotate command
    rotate_parser = subparsers.add_parser('rotate', help='Rotate secrets')
    rotate_parser.add_argument('--service', help='Specific service to rotate')
    rotate_parser.add_argument('--all', action='store_true', help='Rotate all services')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List all secrets')
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up old backups')
    cleanup_parser.add_argument('--days', type=int, default=30, help='Days to keep backups')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    rotator = SecretsRotator(args.project, args.env)
    
    if args.command == 'rotate':
        if args.all:
            rotator.rotate_all_secrets()
        elif args.service:
            if args.service == 'postgres':
                rotator.rotate_postgres_password()
            elif args.service == 'redis':
                rotator.rotate_redis_password()
            elif args.service == 'opensearch':
                rotator.rotate_opensearch_password()
            else:
                log(f"Unknown service: {args.service}")
        else:
            log("Specify --service or --all")
    
    elif args.command == 'list':
        secrets_map = rotator.list_secrets()
        if secrets_map:
            for service, secrets in secrets_map.items():
                log(f"{service}:")
                for secret in secrets:
                    log(f"  - {secret}")
        else:
            log("No secrets found")
    
    elif args.command == 'cleanup':
        rotator.cleanup_old_backups(args.days)

if __name__ == "__main__":
    main()