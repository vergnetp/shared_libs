"""
NginxConfigParser - Query actual nginx configurations as source of truth

This replaces reliance on deployments.json by reading what's actually configured
in nginx. The nginx configs are the real source of truth for what's deployed.
"""

import re
from typing import List, Dict, Any, Optional
from pathlib import Path

try:
    from .resource_resolver import ResourceResolver
except ImportError:
    from resource_resolver import ResourceResolver
try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .server_inventory import ServerInventory
except ImportError:
    from server_inventory import ServerInventory


def log(msg):
    Logger.log(msg)


class NginxConfigParser:
    """
    Parse nginx stream configs to determine what's actually deployed.
    
    Nginx configs are the source of truth because:
    1. Apps connect through nginx (what nginx routes to is what's real)
    2. Configs persist across system restarts
    3. Easy to read/parse programmatically
    """
    
    STREAM_DIR_LINUX = "/etc/nginx/stream.d"
    STREAM_DIR_WINDOWS = "C:/local/nginx/stream.d"
    
    @staticmethod
    def get_stream_config_path(server_ip: str = "localhost", user: str = "root") -> str:
        """Get path to stream config directory for a server"""
        if server_ip == "localhost" or server_ip is None:            
            target_os = ResourceResolver.detect_target_os(None)
            if target_os == "windows":
                return NginxConfigParser.STREAM_DIR_WINDOWS
            else:
                return NginxConfigParser.STREAM_DIR_LINUX
        else:
            return NginxConfigParser.STREAM_DIR_LINUX
    
    @staticmethod
    def list_service_configs(
        server_ip: str = "localhost",
        user: str = "root",
        project: str = None,
        env: str = None
    ) -> List[str]:
        """
        List all nginx stream config files on a server.
        
        Args:
            server_ip: Target server IP
            user: SSH user
            project: Optional filter by project name
            env: Optional filter by environment
            
        Returns:
            List of config filenames (e.g., ["myproj_prod_postgres.conf", ...])
        """
        stream_dir = NginxConfigParser.get_stream_config_path(server_ip, user)
        
        try:
            if server_ip == "localhost" or server_ip is None:
                # Local operation
                path = Path(stream_dir)
                if not path.exists():
                    return []
                
                configs = [f.name for f in path.glob("*.conf")]
            else:
                # Remote operation
                result = CommandExecuter.run_cmd(
                    f"ls {stream_dir}/*.conf 2>/dev/null | xargs -n1 basename || true",
                    server_ip,
                    user
                )
                
                if hasattr(result, 'stdout'):
                    output = result.stdout.strip()
                else:
                    output = str(result).strip()
                
                configs = [line.strip() for line in output.split('\n') if line.strip()]
            
            # Filter by project/env if specified
            filtered = []
            for config in configs:
                # Format: {project}_{env}_{service}.conf
                parts = config.replace('.conf', '').split('_')
                if len(parts) < 3:
                    continue
                
                config_project = parts[0]
                config_env = parts[1]
                
                if project and config_project != project:
                    continue
                if env and config_env != env:
                    continue
                
                filtered.append(config)
            
            return filtered
            
        except Exception as e:
            log(f"Could not list nginx configs on {server_ip}: {e}")
            return []
    
    @staticmethod
    def parse_stream_config(
        config_filename: str,
        server_ip: str = "localhost",
        user: str = "root"
    ) -> Optional[Dict[str, Any]]:
        """
        Parse a nginx stream config file to extract deployment information.
        
        Args:
            config_filename: Config filename (e.g., "myproj_prod_postgres.conf")
            server_ip: Target server IP
            user: SSH user
            
        Returns:
            Dict with:
                - project: Project name
                - env: Environment name
                - service: Service name
                - listen_port: Internal port nginx listens on
                - backends: List of backend dicts with 'target' (container_name or ip:port)
                - mode: 'single_server' or 'multi_server'
                
        Example return for single-server:
            {
                'project': 'myproj',
                'env': 'prod',
                'service': 'postgres',
                'listen_port': 5234,
                'backends': [
                    {'target': 'myproj_prod_postgres:5432'},
                    {'target': 'myproj_prod_postgres_secondary:5432'}
                ],
                'mode': 'single_server'
            }
            
        Example return for multi-server:
            {
                'project': 'myproj',
                'env': 'prod',
                'service': 'api',
                'listen_port': 5890,
                'backends': [
                    {'target': '10.0.0.1:8412'},
                    {'target': '10.0.0.2:18412'}
                ],
                'mode': 'multi_server'
            }
        """
        try:
            # Extract project/env/service from filename
            # Format: {project}_{env}_{service}.conf
            base_name = config_filename.replace('.conf', '')
            parts = base_name.split('_')
            
            if len(parts) < 3:
                log(f"Invalid config filename format: {config_filename}")
                return None
            
            project = parts[0]
            env = parts[1]
            service = '_'.join(parts[2:])  # Service name might contain underscores
            
            # Read config file
            stream_dir = NginxConfigParser.get_stream_config_path(server_ip, user)
            config_path = f"{stream_dir}/{config_filename}"
            
            if server_ip == "localhost" or server_ip is None:
                content = Path(config_path).read_text()
            else:
                result = CommandExecuter.run_cmd(f"cat {config_path}", server_ip, user)
                if hasattr(result, 'stdout'):
                    content = result.stdout
                else:
                    content = str(result)
            
            # Parse config
            listen_port = None
            backends = []
            mode = None
            
            # Extract listen port
            listen_match = re.search(r'listen\s+(\d+);', content)
            if listen_match:
                listen_port = int(listen_match.group(1))
            
            # Extract backend servers
            # Pattern: "server <target>;"
            server_matches = re.finditer(r'server\s+([^;]+);', content)
            for match in server_matches:
                target = match.group(1).strip()
                backends.append({'target': target})
            
            # Determine mode based on backend format
            if backends:
                first_target = backends[0]['target']
                # If contains IP address pattern, it's multi-server
                if re.match(r'\d+\.\d+\.\d+\.\d+:\d+', first_target):
                    mode = 'multi_server'
                else:
                    mode = 'single_server'
            
            return {
                'project': project,
                'env': env,
                'service': service,
                'listen_port': listen_port,
                'backends': backends,
                'mode': mode
            }
            
        except Exception as e:
            log(f"Could not parse nginx config {config_filename}: {e}")
            return None
    
    @staticmethod
    def get_services_on_server(
        server_ip: str,
        user: str = "root",
        project: str = None,
        env: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get all services configured on a server by reading nginx configs.
        
        This replaces DeploymentStateManager.get_services_on_server() with
        live data from actual nginx configs.
        
        Args:
            server_ip: Target server IP
            user: SSH user
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of service dicts with project, env, service, listen_port, backends
        """
        configs = NginxConfigParser.list_service_configs(server_ip, user, project, env)
        
        services = []
        for config_file in configs:
            parsed = NginxConfigParser.parse_stream_config(config_file, server_ip, user)
            if parsed:
                services.append(parsed)
        
        return services
    
    @staticmethod
    def get_expected_containers_on_server(
        server_ip: str,
        user: str = "root",
        project: str = None,
        env: str = None
    ) -> List[str]:
        """
        Get list of container names that should be running on a server
        based on nginx configs.
        
        This is useful for healing: compare expected vs actual containers.
        
        Args:
            server_ip: Target server IP
            user: SSH user
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of container names (e.g., ["myproj_prod_postgres", ...])
        """
        services = NginxConfigParser.get_services_on_server(server_ip, user, project, env)
        
        container_names = []
        for service in services:
            if service['mode'] == 'single_server':
                # Extract container names from backends
                for backend in service['backends']:
                    target = backend['target']
                    # Format: "container_name:port"
                    container_name = target.split(':')[0]
                    if container_name not in container_names:
                        container_names.append(container_name)
        
        return container_names
    
    @staticmethod
    def get_backend_servers_for_service(
        project: str,
        env: str,
        service: str,
        credentials: Dict=None
    ) -> List[str]:
        """
        Get list of server IPs where a service is deployed by querying
        nginx configs on all servers.
        
        This replaces reading from JSON - we query actual configs instead.
        
        Args:
            project: Project name
            env: Environment name
            service: Service name
            credentials: optional dict of credentials
            
        Returns:
            List of server IPs where this service has backends configured
        """       
        all_servers = ServerInventory.list_all_servers(credentials=credentials)
        backend_servers = []
        
        for server in all_servers:
            server_ip = server['ip']
            
            # Check if this server has nginx config for this service
            configs = NginxConfigParser.list_service_configs(
                server_ip, 'root', project, env
            )
            
            service_config_name = f"{project}_{env}_{service}.conf"
            if service_config_name in configs:
                # Parse the config to see if it has backends
                parsed = NginxConfigParser.parse_stream_config(
                    service_config_name, server_ip, 'root'
                )
                
                if parsed and parsed.get('backends'):
                    # This server has the service configured
                    if parsed['mode'] == 'multi_server':
                        # In multi-server mode, extract backend IPs
                        for backend in parsed['backends']:
                            target = backend['target']
                            # Format: "ip:port"
                            backend_ip = target.split(':')[0]
                            if backend_ip not in backend_servers:
                                backend_servers.append(backend_ip)
                    else:
                        # In single-server mode, the server itself runs the containers
                        if server_ip not in backend_servers:
                            backend_servers.append(server_ip)
        
        return backend_servers