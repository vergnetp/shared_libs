import asyncio

from .interface import RegistryAuthenticator


class KubernetesRegistryAuthenticator(RegistryAuthenticator):
    def __init__(self, 
                 service_account: str = None, 
                 namespace: str = 'default'):
        self.service_account = service_account
        self.namespace = namespace
    
    async def authenticate(self, registry_url: str, logger) -> bool:
        """
        Authenticate to a registry using Kubernetes service account
        
        Args:
            registry_url: Registry URL
            logger: Logging instance
        
        Returns:
            bool: Whether authentication was successful
        """
        try:
            # Get service account token
            token_cmd = [
                "kubectl", "get", "secret", 
                f"serviceaccount-{self.service_account or 'default'}", 
                "-n", self.namespace, 
                "-o", "jsonpath={.data.token}"
            ]
            
            # Execute token retrieval
            token_process = await asyncio.create_subprocess_exec(
                *token_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Get token
            stdout, stderr = await token_process.communicate()
            
            if token_process.returncode != 0:
                logger.error(f"Failed to retrieve service account token: {stderr.decode().strip()}")
                return False
            
            # Decode token
            import base64
            token = base64.b64decode(stdout).decode('utf-8')
            
            # Docker login with token
            login_cmd = [
                "docker", "login", 
                "-u", "serviceaccount", 
                "-p", token, 
                registry_url
            ]
            
            # Execute login
            login_process = await asyncio.create_subprocess_exec(
                *login_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Wait for login
            stdout, stderr = await login_process.communicate()
            
            if login_process.returncode == 0:
                logger.info(f"Successfully authenticated to registry via Kubernetes: {registry_url}")
                return True
            else:
                logger.error(f"Kubernetes registry authentication failed: {stderr.decode().strip()}")
                return False
        
        except Exception as e:
            logger.error(f"Error during Kubernetes registry authentication: {e}")
            return False
        