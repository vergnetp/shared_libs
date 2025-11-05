# backend/infra/credentials_manager.py
import os
import json
from pathlib import Path
from typing import Dict

try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .path_resolver import PathResolver
except ImportError:
    from path_resolver import PathResolver
try:
    from .deployment_constants import INFRA_CREDENTIALS_SERVICE_NAME, INFRA_CREDENTIALS_FILENAME
except ImportError:
    from deployment_constants import INFRA_CREDENTIALS_SERVICE_NAME, INFRA_CREDENTIALS_FILENAME


def log(msg):
    Logger.log(msg)


class CredentialsManager:
    """
    Manage infrastructure credentials with per-user/project/env isolation.
    
    Path: /local/{user}/{project}/{env}/secrets/infra/credentials.json
    
    Design:
    - Each deployment context has its own credentials
    - Users can provide different DO tokens per project or environment
    - Health monitor discovers contexts and uses appropriate credentials
    - Complete isolation per DO account
    """
    
    @staticmethod
    def _get_credentials_path(
        user: str,
        project: str,
        env: str,
        server_ip: str = "localhost"
    ) -> Path:
        """
        Get credentials file path using PathResolver.
        
        Treats 'infra' as a service name in secrets/, following the same
        pattern as service passwords (postgres_password, redis_password, etc.)
        
        Args:
            user: User ID (e.g. "u1")
            project: Project name
            env: Environment name
            server_ip: Target server IP for OS detection
            
        Returns:
            Path to credentials file
        """
        secrets_path = PathResolver.get_volume_host_path(
            user, project, env, INFRA_CREDENTIALS_SERVICE_NAME, "secrets", server_ip
        )
        return Path(secrets_path) / INFRA_CREDENTIALS_FILENAME
    
    @staticmethod
    def get_credentials(user: str, project: str, env: str) -> Dict[str, str]:
        """
        Load credentials for a specific deployment context.
        
        Args:
            user: User ID
            project: Project name
            env: Environment name
            
        Returns:
            Credentials dict with: digitalocean_token, docker_hub_user, docker_hub_password
            
        Raises:
            ValueError: If credentials not found
        """
        credentials_file = CredentialsManager._get_credentials_path(
            user, project, env
        )
        
        if credentials_file.exists():
            try:
                creds = json.loads(credentials_file.read_text())
                log(f"Loaded credentials from {credentials_file}")
                return creds
            except Exception as e:
                log(f"Error reading credentials file {credentials_file}: {e}")
        
        # Fallback to environment variables (development/platform defaults)
        do_token = os.getenv('DIGITALOCEAN_API_TOKEN')
        
        if not do_token:
            raise ValueError(
                f"No credentials found for {user}/{project}/{env}! "
                f"Expected at {credentials_file} or DIGITALOCEAN_API_TOKEN env var"
            )
        
        log(f"Using credentials from environment variables for {user}/{project}/{env}")
        
        return {
            'digitalocean_token': do_token,
            'docker_hub_user': os.getenv('DOCKER_HUB_USER'),
            'docker_hub_password': os.getenv('DOCKER_HUB_PASSWORD')
        }
    
    @staticmethod
    def push_credentials_to_server(
        user: str,
        project: str,
        env: str,
        server_ip: str,
        credentials: Dict[str, str],
        ssh_user: str = 'root'
    ) -> bool:
        """
        Push credentials to server.
        
        Args:
            user: User ID
            project: Project name
            env: Environment name
            server_ip: Target server IP
            credentials: Credentials dict
            ssh_user: SSH user (default: 'root')
            
        Returns:
            True if successful
        """
        try:
            credentials_file = CredentialsManager._get_credentials_path(
                user, project, env, server_ip
            )
            credentials_dir = credentials_file.parent
            
            # Create directory
            CommandExecuter.run_cmd(
                f"mkdir -p {credentials_dir}",
                server_ip, ssh_user
            )
            
            # Write credentials (secure permissions)
            creds_json = json.dumps(credentials, indent=2)
            
            CommandExecuter.run_cmd_with_stdin(
                f"cat > {credentials_file} && "
                f"chmod 600 {credentials_file}",
                creds_json.encode('utf-8'),
                server_ip, ssh_user
            )
            
            log(f"✓ Credentials pushed to {server_ip}:{credentials_file}")
            return True
            
        except Exception as e:
            log(f"Failed to push credentials to {server_ip}: {e}")
            return False