import asyncio

from .interface import RegistryAuthenticator


class DockerRegistryAuthenticator(RegistryAuthenticator):
    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
    
    async def authenticate(self, registry_url: str, logger) -> bool:
        """
        Authenticate to a Docker registry (public or private)
        
        Args:
            registry_url: Registry URL
            logger: Logging instance
        
        Returns:
            bool: Whether authentication was successful
        """
        try:
            # Use docker login with username and password
            login_cmd = [
                "docker", "login", 
                "-u", self.username,
                "-p", self.password,
                registry_url
            ]
            
            # Execute login command
            process = await asyncio.create_subprocess_exec(
                *login_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Wait for command to complete
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                logger.info(f"Successfully authenticated to Docker registry: {registry_url}")
                return True
            else:
                logger.error(f"Docker registry authentication failed: {stderr.decode().strip()}")
                return False
        
        except Exception as e:
            logger.error(f"Error during Docker registry authentication: {e}")
            return False

    def get_remote_login_command(self, registry_url: str) -> str:
        return f"docker login {registry_url} -u {self.username} -p {self.password}"       

class AWSECRAuthenticator(RegistryAuthenticator):
    def __init__(self, region: str = 'us-east-1'):
        self.region = region
    
    async def authenticate(self, registry_url: str, logger) -> bool:
        """
        Authenticate to AWS Elastic Container Registry (ECR)
        
        Args:
            registry_url: Registry URL
            logger: Logging instance
        
        Returns:
            bool: Whether authentication was successful
        """
        try:
            # AWS ECR login command
            login_cmd = [
                "aws", "ecr", "get-login-password", 
                "--region", self.region
            ]
            
            # Pipe to docker login
            docker_login_cmd = [
                "docker", "login", 
                "--username", "AWS", 
                "--password-stdin", 
                registry_url
            ]
            
            # Execute AWS login command
            aws_process = await asyncio.create_subprocess_exec(
                *login_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Get AWS login output
            stdout, stderr = await aws_process.communicate()
            
            if aws_process.returncode != 0:
                logger.error(f"AWS ECR login failed: {stderr.decode().strip()}")
                return False
            
            # Pipe AWS login output to docker login
            docker_process = await asyncio.create_subprocess_exec(
                *docker_login_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Send AWS login password
            docker_stdout, docker_stderr = await docker_process.communicate(input=stdout)
            
            if docker_process.returncode == 0:
                logger.info(f"Successfully authenticated to AWS ECR: {registry_url}")
                return True
            else:
                logger.error(f"Docker login to AWS ECR failed: {docker_stderr.decode().strip()}")
                return False
        
        except Exception as e:
            logger.error(f"Error during AWS ECR authentication: {e}")
            return False

