
import json
import socket
import time
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass
from enums import Envs, ServiceTypes
from container_generator import ContainerGenerator
import secrets_manager as sm


class ServiceLocator:
    """
    Static service locator that resolves service endpoints across different environments.
    
    Provides a clean interface to get service endpoints without managing instance state.
    Handles container networking, cross-server communication, and configuration loading.
    """
    
    _config_cache: Dict[str, Any] = {}
    
    @staticmethod
    def get_endpoint(project_name: str, env: Envs, service_type: ServiceTypes, 
                    service_name: str, timeout: int = 5) -> str:
        """
        Get service endpoint in "host:port" format.
        
        Tries multiple connection strategies:
        1. Container networking (same server)
        2. Infrastructure server IP (cross-server) 
        3. Localhost fallback (development)
        
        Args:
            project_name: Name of the project (e.g., "ecommerce")
            env: Environment (DEV, TEST, UAT, PROD)
            service_type: Type of service (POSTGRES, REDIS, OPENSEARCH, WEB, NGINX)
            service_name: Name of the service instance (e.g., "maindb", "cache")
            timeout: Connection test timeout in seconds (default: 5)
            
        Returns:
            str: Endpoint in "host:port" format
            
        Raises:
            ConnectionError: If no connection strategy succeeds
            
        Examples:
            # PostgreSQL endpoint
            endpoint = ServiceLocator.get_endpoint("ecommerce", Envs.PROD, ServiceTypes.POSTGRES, "maindb")
            # Returns: "ecommerce_prod_maindb:5432" (container) or "10.0.1.100:5234" (cross-server)
            
            # Redis with custom timeout
            endpoint = ServiceLocator.get_endpoint("ecommerce", Envs.PROD, ServiceTypes.REDIS, "cache", timeout=2)
            
            # Web service endpoint
            endpoint = ServiceLocator.get_endpoint("ecommerce", Envs.PROD, ServiceTypes.WEB, "api")
        """
        env = Envs.to_enum(env)
        
        # Get configuration and helper objects
        config = ServiceLocator._load_config()
          
        # Generate names and ports
        container_name = ContainerGenerator.generate_container_name(project_name, env, service_name)
        internal_port = ServiceLocator._get_default_port(service_type)
        external_port = ContainerGenerator.hash_port(service_type, project_name, env)
        
        print(f"🔍 Resolving {service_type.value}/{service_name} endpoint...")
        
        # Strategy 1: Try container networking (same-server case)
        container_endpoint = f"{container_name}:{internal_port}"
        print(f"   Trying container networking: {container_endpoint}")
        if ServiceLocator._test_connection(container_name, internal_port, timeout):
            print(f"   ✅ Container connection successful")
            return container_endpoint
        
        print(f"   ❌ Container connection failed")
        
        # Strategy 2: Try infrastructure master server (cross-server case)
        master_ip = config.get("master_server", {}).get("ip", "localhost")
        master_endpoint = f"{master_ip}:{external_port}"
        print(f"   Trying master server: {master_endpoint}")
        if ServiceLocator._test_connection(master_ip, external_port, timeout):
            print(f"   ✅ Master server connection successful")
            return master_endpoint
        
        print(f"   ❌ Master server connection failed")
        
        # Strategy 3: Fallback to localhost (development case)
        localhost_endpoint = f"localhost:{external_port}"
        print(f"   Trying localhost fallback: {localhost_endpoint}")
        if ServiceLocator._test_connection("localhost", external_port, timeout):
            print(f"   ✅ Localhost connection successful")
            return localhost_endpoint
        
        # All strategies failed
        print(f"   ❌ All connection strategies failed for {service_type.value}/{service_name}")
        raise ConnectionError(f"Could not connect to {service_type.value}/{service_name} using any strategy")
    
    @staticmethod
    def get_host_port(project_name: str, env: Envs, service_type: ServiceTypes, 
                     service_name: str, timeout: int = 5) -> tuple[str, int]:
        """
        Get service host and port as separate values.
        
        Args:
            project_name: Name of the project
            env: Environment
            service_type: Type of service
            service_name: Name of the service instance
            timeout: Connection test timeout in seconds
            
        Returns:
            tuple: (host, port) where port is an integer
            
        Examples:
            host, port = ServiceLocator.get_host_port("ecommerce", Envs.PROD, ServiceTypes.POSTGRES, "maindb")
            # Returns: ("ecommerce_prod_maindb", 5432) or ("10.0.1.100", 5234)
        """
        endpoint = ServiceLocator.get_endpoint(project_name, env, service_type, service_name, timeout)
        host, port_str = endpoint.split(':')
        return host, int(port_str)
    
    @staticmethod
    def _load_config(config_path: str = "infrastructure.json") -> Dict[str, Any]:
        """Load and cache infrastructure configuration"""
        if config_path not in ServiceLocator._config_cache:
            try:
                with open(config_path, 'r') as f:
                    ServiceLocator._config_cache[config_path] = json.load(f)
            except FileNotFoundError:
                print(f"Warning: Infrastructure file {config_path} not found, using defaults")
                ServiceLocator._config_cache[config_path] = {
                    "master_server": {"ip": "localhost"},
                    "timeouts": {"default": 5}
                }
        
        return ServiceLocator._config_cache[config_path]
    
    @staticmethod
    def _get_default_port(service_type: ServiceTypes) -> int:
        """Get default internal port for service type"""
        port_map = {
            ServiceTypes.POSTGRES: 5432,
            ServiceTypes.REDIS: 6379,
            ServiceTypes.OPENSEARCH: 9200,
            ServiceTypes.WEB: 8080,
            ServiceTypes.NGINX: 80
        }
        return port_map.get(service_type, 8080)
    
    @staticmethod
    def _test_connection(host: str, port: int, timeout: int = 3) -> bool:
        """Test if we can connect to a host:port"""
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                return True
        except (socket.error, OSError):
            return False
    
    @staticmethod
    def clear_cache():
        """Clear configuration cache (useful for testing)"""
        ServiceLocator._config_cache.clear()


# Convenience functions for common services
def get_postgres_endpoint(project_name: str, env: Envs, service_name: str = "maindb", timeout: int = 10) -> str:
    """Get PostgreSQL endpoint - convenience function"""
    return ServiceLocator.get_endpoint(project_name, env, ServiceTypes.POSTGRES, service_name, timeout)

def get_redis_endpoint(project_name: str, env: Envs, service_name: str = "cache", timeout: int = 5) -> str:
    """Get Redis endpoint - convenience function"""
    return ServiceLocator.get_endpoint(project_name, env, ServiceTypes.REDIS, service_name, timeout)

def get_opensearch_endpoint(project_name: str, env: Envs, service_name: str = "search", timeout: int = 30) -> str:
    """Get OpenSearch endpoint - convenience function"""
    return ServiceLocator.get_endpoint(project_name, env, ServiceTypes.OPENSEARCH, service_name, timeout)

def get_web_endpoint(project_name: str, env: Envs, service_name: str, timeout: int = 5) -> str:
    """Get web service endpoint - convenience function"""
    return ServiceLocator.get_endpoint(project_name, env, ServiceTypes.WEB, service_name, timeout)
