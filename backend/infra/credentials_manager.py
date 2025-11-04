# todo: use PathResolver, deployment constants or whatever instead of hard coded paths
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


def log(msg):
    Logger.log(msg)


class CredentialsManager:
    """Manage infrastructure credentials on droplets"""
    
    CREDENTIALS_FILE = Path("/local/infra/secrets/credentials.json")
    
    @staticmethod
    def get_credentials() -> Dict[str, str]:
        """
        Load credentials with fallback chain:
        1. Secrets file on droplet
        2. Environment variables (for development)
        3. Raise error if not found
        """
        # Try secrets file first
        if CredentialsManager.CREDENTIALS_FILE.exists():
            try:
                return json.loads(CredentialsManager.CREDENTIALS_FILE.read_text())
            except Exception as e:
                log(f"Error reading credentials file: {e}")
        
        # Fallback to environment variables
        do_token = os.getenv('DIGITALOCEAN_API_TOKEN')
        
        if not do_token:
            raise ValueError(
                "No credentials found! Set DIGITALOCEAN_API_TOKEN env var "
                "or create /local/infra/secrets/credentials.json"
            )
        
        return {
            'digitalocean_token': do_token,
            'docker_hub_user': os.getenv('DOCKER_HUB_USER'),
            'docker_hub_password': os.getenv('DOCKER_HUB_PASSWORD')
        }
    
    @staticmethod
    def push_credentials_to_server(
        server_ip: str,
        credentials: Dict[str, str],
        user: str = 'root'
    ) -> bool:
        """Push credentials to server"""
        try:
            # Create directory
            CommandExecuter.run_cmd(
                "mkdir -p /local/infra/secrets",
                server_ip, user
            )
            
            # Write credentials (secure permissions)
            creds_json = json.dumps(credentials, indent=2)
            
            CommandExecuter.run_cmd_with_stdin(
                "cat > /local/infra/secrets/credentials.json && "
                "chmod 600 /local/infra/secrets/credentials.json",
                creds_json.encode('utf-8'),
                server_ip, user
            )
            
            log(f"✓ Credentials pushed to {server_ip}")
            return True
            
        except Exception as e:
            log(f"Failed to push credentials to {server_ip}: {e}")
            return False