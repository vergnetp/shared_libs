"""
Secrets Rotator - Automatic rotation of passwords and API keys.

Usage:
    from shared_libs.backend.vault import SecretsRotator
    
    rotator = SecretsRotator(infisical_token, project_id)
    
    # Rotate a database password
    await rotator.rotate_secret(
        secret_name="POSTGRES_PASSWORD",
        containers=["myapp_prod_api", "myapp_prod_worker"],
        server_ips=["1.2.3.4"],
        on_rotate=lambda old, new: update_db_password(old, new),
    )
"""

import os
import secrets
import string
from typing import Optional, List, Callable, Dict, Any
from dataclasses import dataclass
import asyncio


@dataclass
class RotationResult:
    """Result of a secret rotation."""
    success: bool
    secret_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    error: Optional[str] = None
    containers_updated: List[str] = None
    
    def __post_init__(self):
        if self.containers_updated is None:
            self.containers_updated = []


def generate_password(
    length: int = 32,
    include_special: bool = True,
) -> str:
    """
    Generate a cryptographically secure password.
    
    Args:
        length: Password length (default 32)
        include_special: Include special characters
        
    Returns:
        Random password string
    """
    alphabet = string.ascii_letters + string.digits
    if include_special:
        # Use safe special chars that work in most contexts
        alphabet += "!@#$%^&*"
    
    # Ensure at least one of each type
    password = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
    ]
    if include_special:
        password.append(secrets.choice("!@#$%^&*"))
    
    # Fill rest randomly
    password.extend(secrets.choice(alphabet) for _ in range(length - len(password)))
    
    # Shuffle
    password_list = list(password)
    secrets.SystemRandom().shuffle(password_list)
    
    return ''.join(password_list)


def generate_api_key(prefix: str = "sk") -> str:
    """
    Generate an API key in format: prefix_random32chars
    
    Args:
        prefix: Key prefix (e.g., "sk" for secret key)
        
    Returns:
        API key string
    """
    random_part = secrets.token_urlsafe(24)  # 32 chars base64
    return f"{prefix}_{random_part}"


class InfisicalClient:
    """Client for Infisical secrets management."""
    
    def __init__(
        self,
        token: str,
        project_id: str,
        environment: str = "prod",
        base_url: str = "https://app.infisical.com",
    ):
        self.token = token
        self.project_id = project_id
        self.environment = environment
        self.base_url = base_url
    
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}
    
    def get_secret(self, name: str) -> Optional[str]:
        """Get a secret value."""
        try:
            import requests
            
            url = f"{self.base_url}/api/v3/secrets/raw/{name}"
            params = {
                "workspaceId": self.project_id,
                "environment": self.environment,
            }
            
            resp = requests.get(url, headers=self._headers(), params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json().get("secret", {}).get("secretValue")
            return None
        except Exception as e:
            print(f"[rotator] Failed to get secret {name}: {e}")
            return None
    
    def update_secret(self, name: str, value: str) -> bool:
        """Update a secret value."""
        try:
            import requests
            
            url = f"{self.base_url}/api/v3/secrets/raw/{name}"
            params = {
                "workspaceId": self.project_id,
                "environment": self.environment,
            }
            data = {"secretValue": value}
            
            resp = requests.patch(url, headers=self._headers(), params=params, json=data, timeout=10)
            if resp.status_code == 200:
                return True
            else:
                print(f"[rotator] Failed to update secret {name}: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            print(f"[rotator] Failed to update secret {name}: {e}")
            return False
    
    def create_secret(self, name: str, value: str) -> bool:
        """Create a new secret."""
        try:
            import requests
            
            url = f"{self.base_url}/api/v3/secrets/raw/{name}"
            params = {
                "workspaceId": self.project_id,
                "environment": self.environment,
            }
            data = {
                "secretValue": value,
                "type": "shared",
            }
            
            resp = requests.post(url, headers=self._headers(), params=params, json=data, timeout=10)
            return resp.status_code in (200, 201)
        except Exception as e:
            print(f"[rotator] Failed to create secret {name}: {e}")
            return False


class SecretsRotator:
    """
    Rotate secrets and update dependent containers.
    
    Workflow:
    1. Generate new secret value
    2. Update in vault (Infisical)
    3. Call optional on_rotate callback (e.g., update DB password)
    4. Restart containers with new env var value
    """
    
    def __init__(
        self,
        infisical_token: Optional[str] = None,
        infisical_project_id: Optional[str] = None,
        infisical_env: str = "prod",
        node_agent_key: Optional[str] = None,
    ):
        """
        Initialize rotator.
        
        Args:
            infisical_token: Infisical service token
            infisical_project_id: Infisical project/workspace ID
            infisical_env: Environment (prod, staging, dev)
            node_agent_key: API key for node agents
        """
        self.infisical_token = infisical_token or os.environ.get("INFISICAL_TOKEN")
        self.infisical_project_id = infisical_project_id or os.environ.get("INFISICAL_PROJECT_ID")
        self.infisical_env = infisical_env
        self.node_agent_key = node_agent_key
        
        self.vault = None
        if self.infisical_token and self.infisical_project_id:
            self.vault = InfisicalClient(
                token=self.infisical_token,
                project_id=self.infisical_project_id,
                environment=self.infisical_env,
            )
    
    async def rotate_secret(
        self,
        secret_name: str,
        new_value: Optional[str] = None,
        password_length: int = 32,
        containers: Optional[List[str]] = None,
        server_ips: Optional[List[str]] = None,
        on_rotate: Optional[Callable[[str, str], bool]] = None,
        env_var_name: Optional[str] = None,
    ) -> RotationResult:
        """
        Rotate a secret.
        
        Args:
            secret_name: Name of secret in vault
            new_value: New value (auto-generated if not provided)
            password_length: Length for auto-generated password
            containers: Container names to restart with new env
            server_ips: Server IPs where containers run
            on_rotate: Callback(old_value, new_value) -> success
            env_var_name: Env var name in container (defaults to secret_name)
            
        Returns:
            RotationResult with success status
        """
        containers = containers or []
        server_ips = server_ips or []
        env_var_name = env_var_name or secret_name
        
        try:
            # 1. Get old value
            old_value = None
            if self.vault:
                old_value = self.vault.get_secret(secret_name)
            
            # 2. Generate new value if not provided
            if new_value is None:
                new_value = generate_password(length=password_length)
            
            # 3. Update vault
            if self.vault:
                if old_value is not None:
                    success = self.vault.update_secret(secret_name, new_value)
                else:
                    success = self.vault.create_secret(secret_name, new_value)
                
                if not success:
                    return RotationResult(
                        success=False,
                        secret_name=secret_name,
                        error="Failed to update vault",
                    )
            
            # 4. Call on_rotate callback (e.g., update database password)
            if on_rotate:
                try:
                    callback_success = on_rotate(old_value, new_value)
                    if not callback_success:
                        return RotationResult(
                            success=False,
                            secret_name=secret_name,
                            old_value=old_value,
                            new_value=new_value,
                            error="on_rotate callback failed",
                        )
                except Exception as e:
                    return RotationResult(
                        success=False,
                        secret_name=secret_name,
                        old_value=old_value,
                        new_value=new_value,
                        error=f"on_rotate callback error: {e}",
                    )
            
            # 5. Restart containers with new env var
            updated_containers = []
            if containers and server_ips and self.node_agent_key:
                from .service import get_secret  # Avoid circular import
                
                # Import node agent client
                try:
                    from shared_libs.backend.infra.node_agent import NodeAgentClient
                except ImportError:
                    NodeAgentClient = None
                
                if NodeAgentClient:
                    for server_ip in server_ips:
                        async with NodeAgentClient(server_ip, self.node_agent_key) as client:
                            for container_name in containers:
                                # Get current container config
                                inspect_result = await client._request(
                                    "GET", f"/containers/{container_name}/inspect"
                                )
                                
                                if not inspect_result.success:
                                    continue
                                
                                # Update env var and restart
                                # This requires stopping old and starting new
                                # The proper way is to redeploy, but for hot rotation
                                # we can use docker update (limited) or recreate
                                
                                # For now, just restart - env will be picked up
                                # if using docker-compose or if env comes from external source
                                restart_result = await client.restart_container(container_name)
                                if restart_result.success:
                                    updated_containers.append(f"{server_ip}:{container_name}")
            
            return RotationResult(
                success=True,
                secret_name=secret_name,
                old_value=old_value,
                new_value=new_value,
                containers_updated=updated_containers,
            )
            
        except Exception as e:
            return RotationResult(
                success=False,
                secret_name=secret_name,
                error=str(e),
            )
    
    async def rotate_database_password(
        self,
        secret_name: str,
        db_host: str,
        db_user: str,
        db_name: str = "postgres",
        db_type: str = "postgres",
        containers: Optional[List[str]] = None,
        server_ips: Optional[List[str]] = None,
    ) -> RotationResult:
        """
        Rotate a database password.
        
        This:
        1. Generates new password
        2. Updates the database user's password
        3. Updates vault
        4. Restarts dependent containers
        
        Args:
            secret_name: Vault secret name
            db_host: Database host
            db_user: Database user to update
            db_name: Database name
            db_type: "postgres" or "mysql"
            containers: Containers to restart
            server_ips: Server IPs
            
        Returns:
            RotationResult
        """
        new_password = generate_password(length=32, include_special=False)  # DB-safe chars
        
        def update_db_password(old_pw: str, new_pw: str) -> bool:
            """Update password in database."""
            try:
                if db_type == "postgres":
                    import psycopg2
                    # Connect with old password
                    conn = psycopg2.connect(
                        host=db_host,
                        user=db_user,
                        password=old_pw,
                        database=db_name,
                    )
                    conn.autocommit = True
                    cur = conn.cursor()
                    # Change password
                    cur.execute(f"ALTER USER {db_user} WITH PASSWORD %s", (new_pw,))
                    cur.close()
                    conn.close()
                    return True
                    
                elif db_type == "mysql":
                    import mysql.connector
                    conn = mysql.connector.connect(
                        host=db_host,
                        user=db_user,
                        password=old_pw,
                        database=db_name,
                    )
                    cur = conn.cursor()
                    cur.execute(f"ALTER USER '{db_user}'@'%' IDENTIFIED BY %s", (new_pw,))
                    conn.commit()
                    cur.close()
                    conn.close()
                    return True
                    
                else:
                    print(f"[rotator] Unsupported db_type: {db_type}")
                    return False
                    
            except Exception as e:
                print(f"[rotator] Failed to update DB password: {e}")
                return False
        
        return await self.rotate_secret(
            secret_name=secret_name,
            new_value=new_password,
            containers=containers,
            server_ips=server_ips,
            on_rotate=update_db_password,
        )
