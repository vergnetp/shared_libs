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