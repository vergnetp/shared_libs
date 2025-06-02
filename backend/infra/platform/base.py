"""
Base platform abstraction interfaces for container platforms
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from pathlib import Path


class ContainerRuntime(ABC):
    """Abstract base class for container runtimes"""
    
    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the platform name"""
        pass
    
    @abstractmethod
    def build_image(self, image_name: str, containerfile_path: str, build_context: str, 
                   build_args: Dict[str, str] = None) -> bool:
        """Build container image"""
        pass
    
    @abstractmethod
    def deploy_service(self, config_file: str, working_dir: str = "/opt/app") -> bool:
        """Deploy service using platform-specific configuration"""
        pass
    
    @abstractmethod
    def check_service_status(self, service_name: str) -> str:
        """Check service status - returns 'running', 'stopped', 'not_found', or 'error'"""
        pass
    
    @abstractmethod
    def get_service_logs(self, service_name: str, lines: int = 100) -> str:
        """Get service logs"""
        pass
    
    @abstractmethod
    def stop_service(self, service_name: str) -> bool:
        """Stop service"""
        pass
    
    @abstractmethod
    def remove_service(self, service_name: str) -> bool:
        """Remove service"""
        pass
    
    @abstractmethod
    def restart_service(self, service_name: str) -> bool:
        """Restart service"""
        pass
    
    @abstractmethod
    def get_deploy_command(self, config_file: str) -> str:
        """Get platform-specific deployment command"""
        pass


class TemplateEngine(ABC):
    """Abstract base class for template engines"""
    
    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the platform name"""
        pass
    
    @abstractmethod
    def generate_deployment_config(self, context: Dict[str, Any]) -> str:
        """Generate deployment configuration"""
        pass
    
    @abstractmethod
    def get_config_file_extension(self) -> str:
        """Get configuration file extension"""
        pass
    
    @abstractmethod
    def get_health_check_url(self, service_name: str, host: str, port: int) -> str:
        """Generate health check URL for the platform"""
        pass
    
    @abstractmethod
    def supports_secrets(self) -> bool:
        """Check if platform supports native secret management"""
        pass
    
    @abstractmethod
    def supports_networking(self) -> bool:
        """Check if platform supports advanced networking"""
        pass


class SecretHandler(ABC):
    """Abstract base class for secret handlers"""
    
    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the platform name"""
        pass
    
    @abstractmethod
    def create_secrets(self, project: str, environment: str, secrets: Dict[str, str]) -> List[str]:
        """Create secrets for the platform"""
        pass
    
    @abstractmethod
    def remove_secret(self, secret_name: str, **kwargs) -> bool:
        """Remove a secret"""
        pass
    
    @abstractmethod
    def list_secrets(self, **kwargs) -> List[str]:
        """List all secrets"""
        pass
    
    @abstractmethod
    def cleanup_project_secrets(self, project: str, environment: str) -> int:
        """Remove all secrets for a project/environment"""
        pass
    
    @abstractmethod
    def get_project_secrets(self, project: str, environment: str) -> List[str]:
        """Get all secrets for a project/environment"""
        pass
    
    @abstractmethod
    def validate_secret_availability(self, secret_name: str) -> bool:
        """Validate that a secret exists and is accessible"""
        pass


class PlatformCapabilities:
    """Defines platform capabilities and constraints"""
    
    def __init__(self, platform_name: str):
        self.platform_name = platform_name
        self.supports_secrets = False
        self.supports_networking = False
        self.supports_volumes = False
        self.supports_health_checks = False
        self.supports_auto_scaling = False
        self.supports_rolling_updates = False
        self.max_service_name_length = 63
        self.supported_protocols = ['http', 'https', 'tcp']
        self.supported_image_formats = ['docker']
    
    def validate_service_config(self, config: Dict[str, Any]) -> List[str]:
        """Validate service configuration against platform capabilities"""
        issues = []
        
        service_name = config.get('service_name', '')
        if len(service_name) > self.max_service_name_length:
            issues.append(f"Service name too long: {len(service_name)} > {self.max_service_name_length}")
        
        return issues