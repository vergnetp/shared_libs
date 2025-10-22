# backend/infra/secrets_rotator.py

import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from resource_resolver import ResourceResolver
from logger import Logger
from encryption import Encryption

def log(msg):
    Logger.log(msg)


class SecretsRotator:
    """Handle rotation of secrets for deployed services"""
    
    def __init__(self, project: str, env: str):
        self.project = project
        self.env = env
    
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
    
    def _is_database_password(self, secret_filename: str) -> bool:
        """
        Check if secret file is auto-rotatable.
        Current logic: follow {service}_password pattern
        
        Args:
            secret_filename: Name of secret file
            
        Returns:
            True if it's a database password file
        """
        filename_lower = secret_filename.lower()        
        return filename_lower.find("_password") > -1
    
    def _generate_new_password_locally(self, service_name: str) -> bool:
        """
        Generate new password on BASTION only (no push/restart).
        
        Returns:
            True if successful
        """
        try:
            secrets_dir = ResourceResolver.get_volume_host_path(
                self.project, self.env, service_name, "secrets", "localhost"
            )
            secret_filename = ResourceResolver._get_secret_filename(service_name)
            password_file = Path(secrets_dir) / secret_filename
            
            # Backup existing password
            self._backup_secret(password_file)            

            new_password = Encryption.generate_password()
            
            # Write to bastion
            Path(secrets_dir).mkdir(parents=True, exist_ok=True)
            password_file.write_text(new_password, encoding='utf-8')
            
            log(f"✓ Generated new {service_name} password (bastion)")
            return True
            
        except Exception as e:
            log(f"Failed to generate password: {e}")
            return False
    
    def _get_all_servers(self) -> List[str]:
        """
        Get all server IPs for this project/env.
        
        Returns:
            List of server IP addresses
        """
        from server_inventory import ServerInventory
        
        try:
            servers = ServerInventory.get_servers(                
                deployment_status=ServerInventory.STATUS_ACTIVE
            )
            
            return [server['ip'] for server in servers]
            
        except Exception as e:
            log(f"Error getting servers: {e}")
            return []
    
    def _push_secrets_to_servers(self, server_ips: List[str]):
        """
        Push secrets directory to multiple servers.
        
        Uses DeploymentSyncer.push_directory() for tar + SSH streaming.
        """
        from deployment_syncer import DeploymentSyncer
        
        # Get local secrets base directory
        local_secrets_base = Path(ResourceResolver.get_volume_host_path(
            self.project, self.env, "", "secrets", "localhost"
        ))
        
        if not local_secrets_base.exists():
            log(f"Warning: Local secrets directory not found: {local_secrets_base}")
            return
        
        # Remote base path (parent of secrets directory)
        remote_base_path = f"/local/{self.project}/{self.env}"
        
        # Use DeploymentSyncer's push_directory utility
        log(f"Pushing secrets to {len(server_ips)} server(s)...")
        success = DeploymentSyncer.push_directory(
            local_dir=local_secrets_base,
            remote_base_path=remote_base_path,
            server_ips=server_ips,
            set_permissions=True,  # Secure secrets with proper permissions
            dir_perms="700",
            file_perms="600",
            parallel=True  # Push to all servers in parallel for speed
        )
        
        if not success:
            raise Exception("Failed to push secrets to one or more servers")
    
    
    def _restart_all_services_ordered(self, server_ips: List[str]) -> List[str]:
        """
        Restart ALL services on all servers, respecting startup_order.
        
        Process:
        1. Get all services with their startup_order from config
        2. Group by startup_order
        3. Restart in order: 1, 2, 3, ... (parallel within same order)
        4. Wait between startup_order groups
        
        Args:
            server_ips: List of server IPs
            
        Returns:
            List of successfully restarted services
        """
        from deployment_config import DeploymentConfigurer
        from execute_cmd import CommandExecuter
        import time
        
        try:
            config = DeploymentConfigurer(self.project)
            services = config.get_services(self.env)
            
            if not services:
                log("No services found in config")
                return []
            
            # Group services by startup_order
            services_by_order = {}
            for service_name, service_config in services.items():
                startup_order = service_config.get('startup_order', 1)
                if startup_order not in services_by_order:
                    services_by_order[startup_order] = []
                services_by_order[startup_order].append(service_name)
            
            # Restart in order
            restarted = []
            
            for startup_order in sorted(services_by_order.keys()):
                services_in_group = services_by_order[startup_order]
                
                log(f"\nRestarting startup_order={startup_order}: {', '.join(services_in_group)}")
                
                # Restart all services in this group (parallel across servers)
                for service_name in services_in_group:
                    container_name = ResourceResolver.get_container_name(
                        self.project, self.env, service_name
                    )
                    
                    service_restarted = False
                    
                    for server_ip in server_ips:
                        try:
                            # Check if container exists on this server
                            check_result = CommandExecuter.run_cmd(
                                f"docker ps -a --filter name={container_name} --format '{{{{.Names}}}}'",
                                server_ip,
                                "root"
                            )
                            
                            if container_name in str(check_result):
                                # Restart container
                                CommandExecuter.run_cmd(
                                    f"docker restart {container_name}",
                                    server_ip,
                                    "root",
                                    timeout=60
                                )
                                
                                log(f"  ✓ Restarted {container_name} on {server_ip}")
                                service_restarted = True
                            
                        except Exception as e:
                            log(f"  ❌ Failed to restart {container_name} on {server_ip}: {e}")
                    
                    if service_restarted:
                        restarted.append(service_name)
                
                # Wait between startup_order groups (except after last group)
                if startup_order != max(services_by_order.keys()):
                    log(f"Waiting 10 seconds before next startup_order group...")
                    time.sleep(10)
            
            return restarted
            
        except Exception as e:
            log(f"Error restarting services: {e}")
            return []
    
    def rotate_all_secrets(self, restart_all_services: bool = True) -> Dict[str, Any]:
        """
        Rotate all database passwords (auto-discover from secrets directory).
        
        Process:
        1. Scan secrets directory for database password files
        2. Generate new passwords on BASTION for database services only
        3. Push ALL secrets to all servers (includes manually updated secrets)
        4. Restart ALL services in correct startup order
        
        Args:
            restart_all_services: If True, restarts ALL services after rotation (recommended)
            
        Returns:
            Dict with results:
            {
                'rotated': {'postgres': True, 'redis': True},
                'skipped': {'api': ['openai_key', 'stripe_key']},
                'failed': {},
                'restarted': ['postgres', 'redis', 'api', 'worker']
            }
        """
        results = {
            'rotated': {},
            'skipped': {},
            'failed': {},
            'restarted': []
        }
        
        secrets_map = self.list_secrets()
        
        if not secrets_map:
            log("No secrets found to rotate")
            return results
        
        log(f"Found secrets in {len(secrets_map)} services")
        
        # Step 1: Rotate database passwords only
        for service_name, secret_files in secrets_map.items():
            log(f"\n{'='*60}")
            log(f"Processing service: {service_name}")
            log(f"{'='*60}")

            service_skipped = []

            for secret_file in secret_files:
                auto_rotate = self._is_database_password(secret_file)                
                if auto_rotate:
                    log(f"Detected {secret_file} service - rotating password...") 
                    success = self._generate_new_password_locally(service_name)                    
                    if success:
                        results['rotated'][service_name] = True
                    else:
                        results['failed'][service_name] = "Password generation failed"                
                else:                     
                    secret_name = secret_file.split()[0]  # Remove age info                        
                    log(f"  ⊘ Skipping (not database password): {secret_name}")
                    service_skipped.append(secret_name)
                    
                    
            results['skipped'][service_name] = ','.join(service_skipped)
            
        # Step 2: Push ALL secrets to ALL servers
        log(f"\n{'='*60}")
        log("PUSHING SECRETS TO ALL SERVERS")
        log(f"{'='*60}")
        
        all_servers = self._get_all_servers()
        
        if all_servers:
            log(f"Pushing secrets to {len(all_servers)} server(s)...")
            self._push_secrets_to_servers(all_servers)
        else:
            log("⚠️  No servers found - secrets rotated locally only")
        
        # Step 3: Restart ALL services in correct startup order
        if restart_all_services and all_servers:
            log(f"\n{'='*60}")
            log("RESTARTING ALL SERVICES (respecting startup order)")
            log(f"{'='*60}")
            
            restarted = self._restart_all_services_ordered(all_servers)
            results['restarted'] = restarted
        
        # Summary
        log(f"\n{'='*60}")
        log("ROTATION SUMMARY")
        log(f"{'='*60}")
        log(f"✓ Rotated: {len(results['rotated'])} database services")
        for service in results['rotated'].keys():
            log(f"  • {service}")
        
        if results['skipped']:
            log(f"⊘ Skipped: {len(results['skipped'])} services (non-database secrets)")
            for service, secrets in results['skipped'].items():
                log(f"  • {service}: {', '.join(secrets)}")
        
        if results['restarted']:
            log(f"🔄 Restarted: {len(results['restarted'])} services")
            for service in results['restarted']:
                log(f"  • {service}")
        
        if results['failed']:
            log(f"❌ Failed: {len(results['failed'])} services")
            for service, reason in results['failed'].items():
                log(f"  • {service}: {reason}")
        
        return results
    
    def manual_secret_update(self, service_name: str, secret_name: str, new_value: str, restart_all: bool = True) -> bool:
        """
        Update any secret manually and push to all servers.
        
        Use this for:
        - External API keys (OpenAI, Stripe, AWS, etc.)
        - JWT secrets
        - Any non-database secrets
        
        Process:
        1. Update secret file on BASTION
        2. Push to all servers
        3. Restart ALL services (because we don't know who uses this secret)
        
        Args:
            service_name: Service name
            secret_name: Secret file name (e.g., 'openai_key')
            new_value: New secret value
            restart_all: If True, restarts ALL services (recommended)
            
        Example:
            # After rotating OpenAI key on their platform:
            rotator.manual_secret_update("api", "openai_key", "sk-proj-new-key-here")
        """
        try:
            # Step 1: Update on BASTION
            secrets_dir = ResourceResolver.get_volume_host_path(
                self.project, self.env, service_name, "secrets", "localhost"
            )
            secret_file = Path(secrets_dir) / secret_name
            
            # Backup existing secret
            self._backup_secret(secret_file)
            
            # Write new secret
            Path(secrets_dir).mkdir(parents=True, exist_ok=True)
            secret_file.write_text(new_value, encoding='utf-8')
            
            log(f"✓ Updated secret '{secret_name}' for {service_name} (bastion)")
            
            # Step 2: Push to all servers
            all_servers = self._get_all_servers()
            
            if not all_servers:
                log("⚠️  No servers found - secret updated locally only")
                return True
            
            log(f"\nPushing secrets to {len(all_servers)} server(s)...")
            self._push_secrets_to_servers(all_servers)
            
            # Step 3: Restart ALL services (we don't know who uses this secret)
            if restart_all:
                log(f"\nRestarting ALL services (respecting startup order)...")
                restarted = self._restart_all_services_ordered(all_servers)
                log(f"✓ Restarted {len(restarted)} services")
            else:
                log(f"⚠️  Skipping service restart (restart_all=False)")
                log(f"   You must manually restart services that use this secret!")
            
            return True
            
        except Exception as e:
            log(f"Failed to update secret: {e}")
            return False
    
    def list_secrets(self) -> Dict[str, List[str]]:
        """List all secrets for the project/environment"""
        secrets_base = str(Path(ResourceResolver.get_volume_host_path(
            self.project, self.env, "", "secrets", "localhost"
        )))  # Go up one level to get the secrets base directory
        log(f'Checking {secrets_base}')
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
        secrets_base = Path(ResourceResolver.get_volume_host_path(
            self.project, self.env, "", "secrets", "localhost"
        )).parent  # Go up one level to get the secrets base directory
        
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
    rotate_parser = subparsers.add_parser('rotate', help='Rotate database passwords')
    rotate_parser.add_argument('--no-restart', action='store_true', help='Skip service restart')
    
    # List command
    list_parser = subparsers.add_parser('list', help='List all secrets')
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up old backups')
    cleanup_parser.add_argument('--days', type=int, default=30, help='Days to keep backups')
    
    # Manual update command
    manual_parser = subparsers.add_parser('manual', help='Manually update any secret')
    manual_parser.add_argument('--service', required=True, help='Service name')
    manual_parser.add_argument('--secret', required=True, help='Secret name')
    manual_parser.add_argument('--value', required=True, help='New secret value')
    manual_parser.add_argument('--no-restart', action='store_true', help='Skip service restart')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    rotator = SecretsRotator(args.project, args.env)
    
    if args.command == 'rotate':
        rotator.rotate_all_secrets(restart_all_services=not args.no_restart)
    
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
    
    elif args.command == 'manual':
        rotator.manual_secret_update(
            args.service,
            args.secret,
            args.value,
            restart_all=not args.no_restart
        )

if __name__ == "__main__":
    main()