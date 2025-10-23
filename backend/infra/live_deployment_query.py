"""
LiveDeploymentQuery - Query actual running infrastructure as source of truth

This module provides pure live queries without circular imports.
It queries:
- Running containers (docker ps)
- Nginx configs (/etc/nginx/stream.d/)
- Server inventory (DigitalOcean tags)

NO imports of Deployer or high-level classes to avoid circular dependencies.
"""

from typing import Dict, Any, List, Optional

try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .execute_docker import DockerExecuter
except ImportError:
    from execute_docker import DockerExecuter
try:
    from .server_inventory import ServerInventory
except ImportError:
    from server_inventory import ServerInventory
try:
    from .nginx_config_parser import NginxConfigParser
except ImportError:
    from nginx_config_parser import NginxConfigParser


def log(msg):
    Logger.log(msg)


class LiveDeploymentQuery:
    """
    Query actual running infrastructure to determine deployment state.
    
    This is the SOURCE OF TRUTH for what's actually deployed.
    Pure utility class with no circular dependencies.
    """
    
    @staticmethod
    def get_servers_running_service(
        project: str,
        env: str,
        service: str
    ) -> List[str]:
        """
        Find all servers that have containers running for a service.
        
        Queries docker ps on all servers to find running containers.
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
            
        Returns:
            List of server IPs where the service is running
        """
        all_servers = ServerInventory.list_all_servers()
        servers_with_service = []
        
        container_pattern = f"{project}_{env}_{service}"
        
        for server in all_servers:
            server_ip = server['ip']
            try:
                # Check if this server has containers for this service
                result = CommandExecuter.run_cmd(
                    f"docker ps --filter 'name={container_pattern}' --format '{{{{.Names}}}}'",
                    server_ip,
                    'root'
                )
                
                # Extract container names properly
                if hasattr(result, 'stdout'):
                    output = result.stdout.strip()
                else:
                    output = str(result).strip()
                
                # Filter out garbage lines
                containers = [
                    c.strip() 
                    for c in output.split('\n') 
                    if c.strip() and c.strip().startswith(container_pattern)
                ]
                
                if containers:
                    servers_with_service.append(server_ip)
                    
            except Exception as e:
                log(f"Could not check containers on {server_ip}: {e}")
                continue
        
        return servers_with_service
    
    @staticmethod
    def get_current_deployment(
        project: str,
        env: str,
        service: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get current deployment info by querying live infrastructure.
        
        Process:
        1. Query all servers for running containers matching this service
        2. Get container names from docker ps
        3. Return live deployment info
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
            
        Returns:
            Dict with:
                - servers: List of server IPs
                - container_name: Primary container name (base or _secondary)
                - source: 'live_query'
            Or None if service not found
        """
        # Query live: which servers have this service running?
        servers = LiveDeploymentQuery.get_servers_running_service(project, env, service)
        
        if not servers:
            return None
        
        # Get container info from first server
        container_info = DockerExecuter.find_service_container(
            project, env, service, servers[0]
        )
        
        if not container_info:
            return None
        
        return {
            "servers": servers,
            "container_name": container_info['name'],
            "host_port": container_info.get('port'),
            "source": "live_query"
        }
    
    @staticmethod
    def get_services_on_server(
        server_ip: str,
        project: str = None,
        env: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get all services configured on a server by reading nginx configs.
        
        This queries actual nginx stream configs to see what's deployed.
        
        Args:
            server_ip: Target server IP
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of service dicts with:
                - project: Project name
                - env: Environment name
                - service: Service name
                - container_name: Expected container name
                - listen_port: Internal port nginx listens on
                - source: 'nginx_config'
        """
        # Query nginx configs (source of truth for what's configured)
        services = NginxConfigParser.get_services_on_server(server_ip, 'root', project, env)
        
        result = []
        for svc in services:
            # Get expected container names from nginx config
            container_names = NginxConfigParser.get_expected_containers_on_server(
                server_ip,
                project=svc['project'],
                env=svc['env']
            )
            
            result.append({
                "project": svc['project'],
                "env": svc['env'],
                "service": svc['service'],
                "container_name": container_names[0] if container_names else f"{svc['project']}_{svc['env']}_{svc['service']}",
                "listen_port": svc.get('listen_port'),
                "source": "nginx_config"
            })
        
        return result
    
    @staticmethod
    def get_expected_containers_on_server(
        server_ip: str,
        project: str = None,
        env: str = None
    ) -> List[str]:
        """
        Get list of container names that should be running on a server
        based on nginx configs.
        
        Useful for healing: compare expected vs actual containers.
        
        Args:
            server_ip: Target server IP
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of container names (e.g., ["myproj_prod_postgres", ...])
        """
        return NginxConfigParser.get_expected_containers_on_server(
            server_ip, 'root', project, env
        )
    
    @staticmethod
    def get_actual_containers_on_server(
        server_ip: str,
        project: str = None,
        env: str = None
    ) -> List[str]:
        """
        Get list of containers actually running on a server via docker ps.
        
        Args:
            server_ip: Target server IP
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of container names actually running
        """
        try:
            # Build filter pattern
            if project and env:
                pattern = f"{project}_{env}_"
            elif project:
                pattern = f"{project}_"
            else:
                pattern = ""
            
            cmd = f"docker ps --format '{{{{.Names}}}}'"
            if pattern:
                cmd += f" | grep '{pattern}'"
            
            result = CommandExecuter.run_cmd(cmd, server_ip, 'root')
            
            if hasattr(result, 'stdout'):
                output = result.stdout.strip()
            else:
                output = str(result).strip()
            
            if not output:
                return []
            
            containers = [c.strip() for c in output.split('\n') if c.strip()]
            return containers
            
        except Exception as e:
            log(f"Could not get containers on {server_ip}: {e}")
            return []
    
    @staticmethod
    def compare_expected_vs_actual(
        server_ip: str,
        project: str = None,
        env: str = None
    ) -> Dict[str, List[str]]:
        """
        Compare expected containers (from nginx) vs actual (from docker ps).
        
        Useful for health monitoring and healing.
        
        Args:
            server_ip: Target server IP
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            Dict with:
                - expected: List of containers that should be running
                - actual: List of containers actually running
                - missing: Containers expected but not running
                - unexpected: Containers running but not expected
        """
        expected = LiveDeploymentQuery.get_expected_containers_on_server(
            server_ip, project, env
        )
        actual = LiveDeploymentQuery.get_actual_containers_on_server(
            server_ip, project, env
        )
        
        expected_set = set(expected)
        actual_set = set(actual)
        
        return {
            "expected": expected,
            "actual": actual,
            "missing": list(expected_set - actual_set),
            "unexpected": list(actual_set - expected_set)
        }
    
    @staticmethod
    def get_backend_servers_for_service(
        project: str,
        env: str,
        service: str
    ) -> List[str]:
        """
        Get list of server IPs where a service's backends are configured.
        
        This queries nginx configs across all servers to find where
        the service has backend endpoints configured.
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
            
        Returns:
            List of server IPs where this service has backends
        """
        return NginxConfigParser.get_backend_servers_for_service(
            project, env, service
        )
    
    @staticmethod
    def get_all_running_containers(
        project: str = None,
        env: str = None
    ) -> Dict[str, List[str]]:
        """
        Get all running containers across all servers.
        
        Returns a map of server_ip -> list of container names.
        
        Args:
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            Dict mapping server IPs to lists of container names
        """
        all_servers = ServerInventory.list_all_servers()
        result = {}
        
        for server in all_servers:
            server_ip = server['ip']
            containers = LiveDeploymentQuery.get_actual_containers_on_server(
                server_ip, project, env
            )
            if containers:
                result[server_ip] = containers
        
        return result
    
    @staticmethod
    def is_service_running(
        project: str,
        env: str,
        service: str
    ) -> bool:
        """
        Check if a service is currently running anywhere.
        
        Quick check: does this service have any running containers?
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
            
        Returns:
            True if service has running containers
        """
        servers = LiveDeploymentQuery.get_servers_running_service(project, env, service)
        return len(servers) > 0
    
    @staticmethod
    def get_deployment_summary(
        project: str,
        env: str = None
    ) -> Dict[str, Any]:
        """
        Get a summary of all deployments for a project/env by querying live state.
        
        This provides a live view of what's actually running, not what JSON says.
        
        Args:
            project: Project name
            env: Optional environment filter
            
        Returns:
            Dict with deployment summary:
                - services: Dict of service -> server list
                - total_servers: Total unique servers in use
                - total_containers: Total containers running
        """
        containers_by_server = LiveDeploymentQuery.get_all_running_containers(
            project, env
        )
        
        # Extract service names and group by service
        services = {}
        for server_ip, containers in containers_by_server.items():
            for container_name in containers:
                # Parse container name: {project}_{env}_{service}[_secondary]
                parts = container_name.split('_')
                if len(parts) >= 3:
                    svc_name = parts[2]  # Service is 3rd part
                    
                    if svc_name not in services:
                        services[svc_name] = []
                    
                    if server_ip not in services[svc_name]:
                        services[svc_name].append(server_ip)
        
        total_containers = sum(len(containers) for containers in containers_by_server.values())
        
        return {
            "services": services,
            "total_servers": len(containers_by_server),
            "total_containers": total_containers,
            "servers": list(containers_by_server.keys())
        }