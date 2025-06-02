"""
Platform Abstraction Package

Provides unified interfaces for container platform operations across
Docker, Kubernetes, and other container platforms.
"""

from .manager import PlatformManager
from .base import ContainerRuntime, TemplateEngine, SecretHandler, PlatformCapabilities
from .docker import DockerRuntime, DockerTemplateEngine, DockerSecretHandler
from .kubernetes import KubernetesRuntime, KubernetesTemplateEngine, KubernetesSecretHandler

__all__ = [
    'PlatformManager',
    'ContainerRuntime',
    'TemplateEngine', 
    'SecretHandler',
    'PlatformCapabilities',
    'DockerRuntime',
    'DockerTemplateEngine',
    'DockerSecretHandler',
    'KubernetesRuntime',
    'KubernetesTemplateEngine',
    'KubernetesSecretHandler'
]

# Version info
__version__ = '1.0.0'
__author__ = 'Personal Cloud Orchestration System'

# Platform registry for easy access
AVAILABLE_PLATFORMS = {
    'docker': {
        'runtime': DockerRuntime,
        'template_engine': DockerTemplateEngine,
        'secret_handler': DockerSecretHandler,
        'description': 'Docker with Docker Compose'
    },
    'kubernetes': {
        'runtime': KubernetesRuntime,
        'template_engine': KubernetesTemplateEngine,
        'secret_handler': KubernetesSecretHandler,
        'description': 'Kubernetes with YAML manifests'
    }
}

def get_available_platforms():
    """Get list of available platform names"""
    return list(AVAILABLE_PLATFORMS.keys())

def get_platform_info(platform_name: str):
    """Get information about a specific platform"""
    return AVAILABLE_PLATFORMS.get(platform_name)

def create_platform_manager(platform: str = 'docker', secret_manager=None):
    """Factory function to create a platform manager"""
    return PlatformManager(platform=platform, secret_manager=secret_manager)