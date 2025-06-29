from enum import Enum

class Envs(Enum):
    DEV = "dev"
    TEST = "test" 
    UAT = "uat"
    PROD = "prod"

    @staticmethod
    def to_enum(value):
        """
        Convert string to Envs enum if needed, or return existing enum.
        
        Args:
            value: Either a string ("dev", "prod", etc.) or an Envs enum
            
        Returns:
            Envs: The corresponding Envs enum value
            
        Examples:
            Envs.ensure_enum("dev") -> Envs.DEV
            Envs.ensure_enum(Envs.PROD) -> Envs.PROD
            Envs.ensure_enum("invalid") -> raises ValueError
            
        Raises:
            ValueError: If value is not a valid environment string or Envs enum
        """
        if isinstance(value, str):
            return Envs(value)
        elif isinstance(value, Envs):
            return value
        else:
            raise ValueError(f"Expected str or Envs enum, got {type(value)}: {value}")

class ServiceTypes(Enum):
    WEB = "web"
    WORKER = "worker"
    POSTGRES = "postgres"
    REDIS = "redis"
    OPENSEARCH = "opensearch"
    NGINX = "nginx"