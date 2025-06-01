from abc import ABC, abstractmethod

class RegistryAuthenticator(ABC):
    @abstractmethod
    async def authenticate(self, registry_url: str, logger) -> bool:
        """
        Authenticate to a container registry_url
        
        Args:
            registry_url: Registry URL
            logger: Logging instance
        
        Returns:
            bool: Whether authentication was successful
        """
        pass

    @abstractmethod
    def get_remote_login_command(self, registry_url: str) -> str:
        """
        Generate a login command that can be executed on a remote server
        
        Args:
            registry_url: Registry URL
        
        Returns:
            str: Full shell command to authenticate to the registry
        """
        pass