# backend/infra/credentials_updater.py
"""
Credentials Update & Rotation

Allows users to update their stored credentials after rotation,
without needing to redeploy everything.
"""

from typing import Dict, List, Optional
from pathlib import Path

try:
    from .credentials_manager import CredentialsManager
except ImportError:
    from credentials_manager import CredentialsManager
try:
    from .server_inventory import ServerInventory
except ImportError:
    from server_inventory import ServerInventory
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .deployment_config import DeploymentConfigurer
except ImportError:
    from deployment_config import DeploymentConfigurer


def log(msg):
    Logger.log(msg)


class CredentialsUpdater:
    """
    Update stored credentials across all servers.
    
    Use cases:
    - User rotated their DigitalOcean token
    - User changed Docker Hub password
    - User wants to update any stored credential
    
    This updates credentials on ALL servers where this user/project/env is deployed,
    ensuring health monitor and future operations use the new credentials.
    """
    
    @staticmethod
    def update_credentials(
        user: str,
        project: str,
        env: str,
        new_credentials: Dict[str, str],
        verify: bool = True
    ) -> Dict[str, bool]:
        """
        Update credentials for a specific deployment context on all servers.
        
        Args:
            user: User ID (e.g., "userB")
            project: Project name (e.g., "my-api")
            env: Environment name (e.g., "prod")
            new_credentials: New credentials dict with keys:
                - digitalocean_token (optional)
                - docker_hub_user (optional)
                - docker_hub_password (optional)
            verify: If True, verify new DO token works before updating (default: True)
            
        Returns:
            Dict mapping server IP to success status
            
        Example:
            result = CredentialsUpdater.update_credentials(
                "userB", "my-api", "prod",
                {'digitalocean_token': 'NEW_TOKEN'}
            )
            # {'192.168.1.100': True, '192.168.1.101': True}
        """
        log(f"Updating credentials for {user}/{project}/{env}")
        Logger.start()
        
        # Step 1: Load existing credentials
        try:
            existing_creds = CredentialsManager.get_credentials(user, project, env)
            log(f"✓ Loaded existing credentials")
        except ValueError:
            log(f"⚠️  No existing credentials found, will create new")
            existing_creds = {}
        
        # Step 2: Merge new credentials with existing
        updated_creds = {**existing_creds, **new_credentials}
        
        # Step 3: Verify new DO token works (if provided and verify=True)
        if verify and 'digitalocean_token' in new_credentials:
            log("Verifying new DigitalOcean token...")
            if not CredentialsUpdater._verify_do_token(new_credentials['digitalocean_token']):
                log("✗ New DO token verification failed!")
                Logger.end()
                return {}
            log("✓ New DO token verified")
        
        # Step 4: Get all servers where this context is deployed
        log("Finding servers with this deployment...")
        
        try:
            # Use old credentials to list servers
            servers = ServerInventory.list_all_servers(credentials=existing_creds)
            
            # Filter to servers in zones used by this project/env
            config = DeploymentConfigurer(user, project)
            services = config.get_services(env)
            
            zones = set()
            for svc_name, svc_config in services.items():
                zone = svc_config.get('server_zone', 'lon1')
                if zone != 'localhost':
                    zones.add(zone)
            
            target_servers = [s for s in servers if s['zone'] in zones]
            
            if not target_servers:
                log("⚠️  No remote servers found for this deployment")
                Logger.end()
                return {}
            
            log(f"Found {len(target_servers)} servers to update")
            
        except Exception as e:
            log(f"Failed to list servers: {e}")
            Logger.end()
            return {}
        
        # Step 5: Update credentials on each server
        results = {}
        
        for server in target_servers:
            server_ip = server['ip']
            log(f"Updating {server_ip}...")
            
            try:
                success = CredentialsManager.push_credentials_to_server(
                    user, project, env,
                    server_ip,
                    updated_creds
                )
                results[server_ip] = success
                
                if success:
                    log(f"  ✓ Updated successfully")
                else:
                    log(f"  ✗ Update failed")
                    
            except Exception as e:
                log(f"  ✗ Error: {e}")
                results[server_ip] = False
        
        # Step 6: Update local credentials file
        log("Updating local credentials file...")
        try:
            local_creds_path = CredentialsManager._get_credentials_path(
                user, project, env, "localhost"
            )
            local_creds_path.parent.mkdir(parents=True, exist_ok=True)
            
            import json
            local_creds_path.write_text(json.dumps(updated_creds, indent=2))
            
            log(f"✓ Local credentials updated")
        except Exception as e:
            log(f"⚠️  Failed to update local credentials: {e}")
        
        # Summary
        success_count = sum(1 for v in results.values() if v)
        total_count = len(results)
        
        Logger.end()
        log(f"Update complete: {success_count}/{total_count} servers updated successfully")
        
        return results
    
    @staticmethod
    def _verify_do_token(token: str) -> bool:
        """
        Verify DigitalOcean token works by making a test API call.
        
        Args:
            token: DO API token to verify
            
        Returns:
            True if token is valid
        """
        try:
            import requests
            
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(
                "https://api.digitalocean.com/v2/account",
                headers=headers,
                timeout=10
            )
            
            return response.status_code == 200
            
        except Exception as e:
            log(f"Token verification failed: {e}")
            return False
    
    @staticmethod
    def rotate_token(
        user: str,
        project: str,
        env: str,
        old_token: str,
        new_token: str
    ) -> bool:
        """
        Helper method specifically for token rotation.
        
        Verifies both old and new tokens before updating.
        
        Args:
            user: User ID
            project: Project name
            env: Environment name
            old_token: Current token (for verification)
            new_token: New token after rotation
            
        Returns:
            True if rotation successful
            
        Example:
            success = CredentialsUpdater.rotate_token(
                "userB", "my-api", "prod",
                old_token="dop_v1_OLD...",
                new_token="dop_v1_NEW..."
            )
        """
        log(f"Rotating DigitalOcean token for {user}/{project}/{env}")
        Logger.start()
        
        # Verify old token matches what's stored
        try:
            current_creds = CredentialsManager.get_credentials(user, project, env)
            stored_token = current_creds.get('digitalocean_token')
            
            if stored_token != old_token:
                log("✗ Old token doesn't match stored token!")
                log("  This is a security check to prevent unauthorized rotation")
                Logger.end()
                return False
                
        except Exception as e:
            log(f"✗ Could not verify old token: {e}")
            Logger.end()
            return False
        
        # Verify new token works
        log("Verifying new token...")
        if not CredentialsUpdater._verify_do_token(new_token):
            log("✗ New token verification failed!")
            Logger.end()
            return False
        
        log("✓ New token verified")
        
        # Update credentials
        results = CredentialsUpdater.update_credentials(
            user, project, env,
            {'digitalocean_token': new_token},
            verify=False  # Already verified above
        )
        
        success = all(results.values()) if results else False
        
        Logger.end()
        
        if success:
            log("✓ Token rotation complete!")
            log("  Health monitor will use new token on next run")
        else:
            log("✗ Token rotation failed on some servers")
            log("  Check logs above for details")
        
        return success
    
    @staticmethod
    def update_docker_credentials(
        user: str,
        project: str,
        env: str,
        docker_hub_user: Optional[str] = None,
        docker_hub_password: Optional[str] = None
    ) -> bool:
        """
        Update Docker Hub credentials only.
        
        Args:
            user: User ID
            project: Project name
            env: Environment name
            docker_hub_user: New Docker Hub username (optional)
            docker_hub_password: New Docker Hub password (optional)
            
        Returns:
            True if update successful
            
        Example:
            CredentialsUpdater.update_docker_credentials(
                "userB", "my-api", "prod",
                docker_hub_password="NEW_PASSWORD"
            )
        """
        new_creds = {}
        
        if docker_hub_user:
            new_creds['docker_hub_user'] = docker_hub_user
        
        if docker_hub_password:
            new_creds['docker_hub_password'] = docker_hub_password
        
        if not new_creds:
            log("No Docker credentials provided to update")
            return False
        
        results = CredentialsUpdater.update_credentials(
            user, project, env,
            new_creds,
            verify=False  # No verification for Docker creds
        )
        
        return all(results.values()) if results else False
    
    @staticmethod
    def list_stored_credentials(user: str, project: str, env: str) -> Dict[str, str]:
        """
        List which credentials are currently stored (without showing values).
        
        Args:
            user: User ID
            project: Project name
            env: Environment name
            
        Returns:
            Dict showing which credentials exist (values are masked)
            
        Example:
            creds = CredentialsUpdater.list_stored_credentials("userB", "my-api", "prod")
            # {
            #   'digitalocean_token': '****....(64 chars)',
            #   'docker_hub_user': 'userb',
            #   'docker_hub_password': '****....(12 chars)'
            # }
        """
        try:
            creds = CredentialsManager.get_credentials(user, project, env)
            
            masked = {}
            for key, value in creds.items():
                if value:
                    if 'password' in key or 'token' in key or 'secret' in key:
                        # Mask sensitive values
                        masked[key] = f"****....({len(value)} chars)"
                    else:
                        # Show non-sensitive values
                        masked[key] = value
                else:
                    masked[key] = None
            
            return masked
            
        except Exception as e:
            log(f"Could not load credentials: {e}")
            return {}
