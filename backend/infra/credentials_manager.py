# backend/infra/credentials_manager.py
import os
import json
from pathlib import Path
from typing import Dict

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
    
    Security:
    - Uses HTTP agent (no SSH required)
    - File permissions: 600 (owner read/write only)
    - Credentials never logged in full
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
                # Security: Don't log the actual file path or contents
                log(f"✓ Loaded credentials for {user}/{project}/{env}")
                return creds
            except Exception as e:
                log(f"Error reading credentials for {user}/{project}/{env}: {e}")
        
        # Fallback to environment variables (development/platform defaults)
        do_token = os.getenv('DIGITALOCEAN_API_TOKEN')
        
        if not do_token:
            raise ValueError(
                f"No credentials found for {user}/{project}/{env}! "
                f"Provide credentials dict or set DIGITALOCEAN_API_TOKEN"
            )
        
        log(f"Using environment variables for {user}/{project}/{env}")
        
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
        credentials: Dict[str, str]
    ) -> bool:
        """
        Push credentials to server via HTTP agent (no SSH required).
        
        Args:
            user: User ID
            project: Project name
            env: Environment name
            server_ip: Target server IP
            credentials: Credentials dict            
            
        Returns:
            True if successful
            
        Security:
        - Uses HTTP agent with API key authentication
        - Sets file permissions to 600 (owner read/write only)
        - Credentials transmitted over VPC-only connection
        """
        try:
            # Import here to avoid circular dependency
            try:
                from .health_monitor import HealthMonitor
            except ImportError:
                from health_monitor import HealthMonitor
            
            credentials_file = CredentialsManager._get_credentials_path(
                user, project, env, server_ip
            )
            
            # Prepare credentials JSON
            creds_json = json.dumps(credentials, indent=2)
            
            # Push via HTTP agent (no SSH!)
            HealthMonitor.agent_request(
                server_ip,
                "POST",
                "/credentials/write",
                json_data={
                    'path': str(credentials_file),
                    'content': creds_json,
                    'permissions': '600'
                },
                timeout=30
            )
            
            # Security: Don't log the actual path or contents
            log(f"✓ Credentials pushed to {server_ip} for {user}/{project}/{env}")
            return True
            
        except Exception as e:
            log(f"Failed to push credentials to {server_ip}: {e}")
            return False