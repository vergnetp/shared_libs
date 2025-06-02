"""
Platform manager that coordinates all platform-specific operations
"""

from typing import Dict, Any, Optional, List
from .base import ContainerRuntime, TemplateEngine, SecretHandler
from .docker import DockerRuntime, DockerTemplateEngine, DockerSecretHandler
from .kubernetes import KubernetesRuntime, KubernetesTemplateEngine, KubernetesSecretHandler


class PlatformManager:
    """
    Manages platform-specific operations across different container platforms
    """
    
    def __init__(self, platform: str = 'docker', secret_manager=None):
        self.platform = platform
        self.secret_manager = secret_manager
        
        # Validate platform
        if platform not in self.get_available_platforms():
            raise ValueError(f"Unsupported platform: {platform}. Available: {self.get_available_platforms()}")
        
        # Initialize platform-specific components
        self.runtime = self._get_runtime(platform)
        self.template_engine = self._get_template_engine(platform)
        self.secret_handler = self._get_secret_handler(platform, secret_manager)
        
        print(f"Initialized platform manager for {platform}")
    
    def _get_runtime(self, platform: str) -> ContainerRuntime:
        """Get platform-specific runtime"""
        runtimes = {
            'docker': DockerRuntime,
            'kubernetes': KubernetesRuntime,
        }
        
        runtime_class = runtimes.get(platform)
        if not runtime_class:
            raise ValueError(f"Unsupported platform runtime: {platform}")
        
        return runtime_class()
    
    def _get_template_engine(self, platform: str) -> TemplateEngine:
        """Get platform-specific template engine"""
        engines = {
            'docker': DockerTemplateEngine,
            'kubernetes': KubernetesTemplateEngine,
        }
        
        engine_class = engines.get(platform)
        if not engine_class:
            raise ValueError(f"Unsupported template engine for platform: {platform}")
        
        return engine_class()
    
    def _get_secret_handler(self, platform: str, secret_manager) -> SecretHandler:
        """Get platform-specific secret handler"""
        if not secret_manager:
            raise ValueError("Secret manager is required for platform initialization")
        
        handlers = {
            'docker': DockerSecretHandler,
            'kubernetes': KubernetesSecretHandler,
        }
        
        handler_class = handlers.get(platform)
        if not handler_class:
            raise ValueError(f"Unsupported secret handler for platform: {platform}")
        
        return handler_class(secret_manager)
    
    @classmethod
    def get_available_platforms(cls) -> List[str]:
        """Get list of available platforms"""
        return ['docker', 'kubernetes']
    
    def get_platform_name(self) -> str:
        """Get current platform name"""
        return self.platform
    
    def get_platform_capabilities(self) -> Dict[str, bool]:
        """Get platform capabilities"""
        return {
            'platform': self.platform,
            'supports_secrets': self.template_engine.supports_secrets(),
            'supports_networking': self.template_engine.supports_networking(),
            'supports_auto_scaling': hasattr(self.runtime, 'capabilities') and 
                                   getattr(self.runtime.capabilities, 'supports_auto_scaling', False),
            'supports_rolling_updates': hasattr(self.runtime, 'capabilities') and 
                                      getattr(self.runtime.capabilities, 'supports_rolling_updates', False)
        }
    
    # Image Management
    def build_image(self, image_name: str, containerfile_path: str, build_context: str, 
                   build_args: Dict[str, str] = None) -> bool:
        """Build container image using platform-specific method"""
        return self.runtime.build_image(image_name, containerfile_path, build_context, build_args)
    
    # Service Deployment
    def deploy_service(self, context: Dict[str, Any], config_file_path: str) -> bool:
        """Deploy service using platform-specific configuration"""
        return self.runtime.deploy_service(config_file_path)
    
    def generate_deployment_config(self, context: Dict[str, Any]) -> str:
        """Generate platform-specific deployment configuration"""
        # Add platform-specific secrets configuration
        context = self._enhance_context_with_secrets(context)
        return self.template_engine.generate_deployment_config(context)
    
    def _enhance_context_with_secrets(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Enhance context with platform-specific secrets configuration"""
        project = context.get('project', '')
        environment = context.get('environment', '')
        service_config = context.get('service_config', {})
        required_secrets = service_config.get('secrets', [])
        
        if not required_secrets:
            return context
        
        # Generate secrets configuration based on platform
        if self.platform == 'docker':
            secrets_config = {
                "type": "docker_secrets",
                "secrets": [f"{project}_{environment}_{secret}" for secret in required_secrets]
            }
        elif self.platform == 'kubernetes':
            secrets_config = {
                "type": "kubernetes_secret",
                "secret_name": f"{project}-{environment}-secrets",
                "secret_keys": required_secrets
            }
        else:
            secrets_config = {"type": "none"}
        
        # Add to context
        enhanced_context = context.copy()
        enhanced_context['secrets_config'] = secrets_config
        return enhanced_context
    
    def get_config_file_name(self, service_name: str) -> str:
        """Get platform-specific configuration file name"""
        extension = self.template_engine.get_config_file_extension()
        return f"{service_name}-{self.platform}.{extension}"
    
    def get_deploy_command(self, config_file: str) -> str:
        """Get platform-specific deployment command"""
        return self.runtime.get_deploy_command(config_file)
    
    # Service Management
    def check_service_status(self, service_name: str) -> str:
        """Check service status"""
        return self.runtime.check_service_status(service_name)
    
    def get_service_logs(self, service_name: str, lines: int = 100) -> str:
        """Get service logs"""
        return self.runtime.get_service_logs(service_name, lines)
    
    def stop_service(self, service_name: str) -> bool:
        """Stop service"""
        return self.runtime.stop_service(service_name)
    
    def remove_service(self, service_name: str) -> bool:
        """Remove service"""
        return self.runtime.remove_service(service_name)
    
    def restart_service(self, service_name: str) -> bool:
        """Restart service"""
        return self.runtime.restart_service(service_name)
    
    # Secret Management
    def create_secrets(self, project: str, environment: str, secrets: Dict[str, str]) -> List[str]:
        """Create secrets using platform-specific method"""
        if not secrets:
            return []
        return self.secret_handler.create_secrets(project, environment, secrets)
    
    def remove_secret(self, secret_name: str, **kwargs) -> bool:
        """Remove a secret"""
        return self.secret_handler.remove_secret(secret_name, **kwargs)
    
    def list_secrets(self, **kwargs) -> List[str]:
        """List all secrets"""
        return self.secret_handler.list_secrets(**kwargs)
    
    def cleanup_project_secrets(self, project: str, environment: str) -> int:
        """Remove all secrets for a project/environment"""
        return self.secret_handler.cleanup_project_secrets(project, environment)
    
    def get_project_secrets(self, project: str, environment: str) -> List[str]:
        """Get all secrets for a project/environment"""
        return self.secret_handler.get_project_secrets(project, environment)
    
    def validate_secret_availability(self, secret_name: str, **kwargs) -> bool:
        """Validate that a secret exists and is accessible"""
        return self.secret_handler.validate_secret_availability(secret_name, **kwargs)
    
    # Health Checks
    def get_health_check_url(self, service_name: str, host: str, port: int) -> str:
        """Generate health check URL"""
        return self.template_engine.get_health_check_url(service_name, host, port)
    
    # Platform Information
    def get_platform_info(self) -> Dict[str, Any]:
        """Get comprehensive platform information"""
        return {
            'platform': self.platform,
            'runtime': self.runtime.get_platform_name(),
            'template_engine': self.template_engine.get_platform_name(),
            'secret_handler': self.secret_handler.get_platform_name(),
            'capabilities': self.get_platform_capabilities(),
            'config_extension': self.template_engine.get_config_file_extension()
        }
    
    # Validation
    def validate_service_configuration(self, context: Dict[str, Any]) -> List[str]:
        """Validate service configuration for the platform"""
        issues = []
        
        # Basic validation
        required_fields = ['service_name', 'image_name', 'project', 'environment']
        for field in required_fields:
            if field not in context or not context[field]:
                issues.append(f"Missing required field: {field}")
        
        # Platform-specific validation
        if hasattr(self.runtime, 'capabilities'):
            platform_issues = self.runtime.capabilities.validate_service_config(context)
            issues.extend(platform_issues)
        
        # Secret validation
        service_config = context.get('service_config', {})
        required_secrets = service_config.get('secrets', [])
        if required_secrets and not self.template_engine.supports_secrets():
            issues.append(f"Platform {self.platform} does not support native secrets")
        
        # Networking validation
        if context.get('SERVICE_PORT') and not self.template_engine.supports_networking():
            issues.append(f"Platform {self.platform} does not support advanced networking")
        
        return issues
    
    # Utility Methods
    def switch_platform(self, new_platform: str) -> bool:
        """Switch to a different platform"""
        try:
            if new_platform not in self.get_available_platforms():
                raise ValueError(f"Unsupported platform: {new_platform}")
            
            # Reinitialize components
            self.platform = new_platform
            self.runtime = self._get_runtime(new_platform)
            self.template_engine = self._get_template_engine(new_platform)
            self.secret_handler = self._get_secret_handler(new_platform, self.secret_manager)
            
            print(f"Switched platform manager to {new_platform}")
            return True
            
        except Exception as e:
            print(f"Failed to switch platform to {new_platform}: {e}")
            return False