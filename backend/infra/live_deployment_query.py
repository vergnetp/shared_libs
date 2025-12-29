# backend/infra/live_deployment_query.py
"""
LiveDeploymentQuery - Query actual running infrastructure as source of truth

This module provides pure live queries without circular imports.
It queries:
- Running containers (docker ps)
- Nginx configs (/etc/nginx/stream.d/)
- Deployment configs (via PathResolver)
- Server inventory (DigitalOcean tags)

NO imports of Deployer or high-level classes to avoid circular dependencies.
"""
import fnmatch
import json
from typing import Dict, Any, List, Optional
from pathlib import Path

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
try:
    from .path_resolver import PathResolver
except ImportError:
    from path_resolver import PathResolver
try:
    from .resource_resolver import ResourceResolver
except ImportError:
    from resource_resolver import ResourceResolver
try:
    from .deployment_naming import DeploymentNaming
except ImportError:
    from deployment_naming import DeploymentNaming
try:
    from .deployment_constants import DEPLOYMENT_CONFIG_SERVICE_NAME, DEPLOYMENT_CONFIG_FILENAME
except ImportError:
    from deployment_constants import DEPLOYMENT_CONFIG_SERVICE_NAME, DEPLOYMENT_CONFIG_FILENAME


def log(msg):
    Logger.log(msg)


# Re-export constants for backward compatibility
# (but import from deployment_constants is preferred)


class LiveDeploymentQuery:
    """
    Query actual running infrastructure to determine deployment state.
    
    This is the SOURCE OF TRUTH for what's actually deployed.
    Pure utility class with no circular dependencies.
    """
    
    @staticmethod
    def _parse_container_name_or_pattern(name_or_pattern: str) -> Optional[Dict[str, str]]:
        """
        Parse container name or pattern to extract components.
        
        CRITICAL: This delegates to DeploymentNaming which is the SINGLE source
        of truth for container naming structure. Never parse manually!
        
        Args:
            name_or_pattern: Container name or pattern (e.g., "u1_myapp_prod_api" or "u1_myapp_prod_api*")
            
        Returns:
            Dict with keys: user, project, env, service
            Or None if parsing fails
            
        Examples:
            _parse_container_name_or_pattern("u1_myapp_prod_api") 
            -> {"user": "u1", "project": "myapp", "env": "prod", "service": "api"}
            
            _parse_container_name_or_pattern("u1_myapp_prod_cleanup_job*")
            -> {"user": "u1", "project": "myapp", "env": "prod", "service": "cleanup_job"}
        """
        # Strip wildcard if present
        name = name_or_pattern.rstrip('*')
        
        # Delegate to DeploymentNaming for parsing
        # This way if naming convention changes, we only update DeploymentNaming
        return DeploymentNaming.parse_container_name(name)
    
    @staticmethod
    def get_servers_running_service(
        credentials: Optional[Dict[str, str]],
        user: str,
        project: str,
        env: str,
        service: str       
    ) -> List[str]:
        """
        Find all servers that have containers running for a service.
        
        Queries docker ps on all servers to find running containers.
        
        Args:
            credentials: optional dict of credentials
            user: user id
            project: Project name
            env: Environment name
            service: Service name
            
            
        Returns:
            List of server IPs where the service is running
        """
        all_servers = ServerInventory.list_all_servers(credentials)
        servers_with_service = []
        
        # Use ResourceResolver for container naming pattern
        container_pattern = ResourceResolver.get_container_name(user, project, env, service)
        
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
        credentials: Optional[Dict[str, str]],
        user: str,
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
            credentials: Dict=None
            user: user id
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
        servers = LiveDeploymentQuery.get_servers_running_service(credentials, user, project, env, service)
        
        if not servers:
            return None
        
        # Get container info from first server
        container_info = DockerExecuter.find_service_container(
            user, project, env, service, servers[0]
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
        user: str,
        project: str = None,
        env: str = None
    ) -> List[Dict[str, Any]]:
        """
        Get all services configured on a server by reading nginx configs.
        
        This queries actual nginx stream configs to see what's deployed.
        
        Args:
            server_ip: Target server IP
            user: user id
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
                user,
                project=svc['project'],
                env=svc['env']
            )
            
            # Use ResourceResolver for container naming
            default_name = ResourceResolver.get_container_name(
                user, svc['project'], svc['env'], svc['service']
            )
            
            result.append({
                "project": svc['project'],
                "env": svc['env'],
                "service": svc['service'],
                "container_name": container_names[0] if container_names else default_name,
                "listen_port": svc.get('listen_port'),
                "source": "nginx_config"
            })
        
        return result
    

    @staticmethod
    def get_expected_containers_on_server(
        server_ip: str,
        user: str,
        project: str = None,
        env: str = None
    ) -> List[str]:
        """
        Get containers that SHOULD be running on server.
        
        Uses multiple sources for robustness:
        1. Deployment configs (via PathResolver) - PRIMARY
        2. Nginx configs - SECONDARY
        3. Crontab - VERIFICATION (for scheduled jobs)
        4. Running containers - FALLBACK
        
        Args:
            server_ip: Target server IP
            user: user id
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of container name patterns
        """
        patterns = []
        
        # PRIMARY: Get from deployment configs
        config_patterns = LiveDeploymentQuery._get_patterns_from_deployment_configs(
            server_ip, user, project, env
        )
        patterns.extend(config_patterns)
        
        # SECONDARY: Get from nginx configs
        nginx_patterns = NginxConfigParser.get_expected_container_patterns(
            server_ip, user, project, env
        )
        patterns.extend(nginx_patterns)
        
        # VERIFICATION: Check crontab for scheduled jobs
        cron_patterns = LiveDeploymentQuery._get_scheduled_patterns_from_cron(
            server_ip, user, project, env
        )
        patterns.extend(cron_patterns)
        
        # FALLBACK: Get from running containers if nothing found
        if not patterns:
            running_patterns = LiveDeploymentQuery._get_patterns_from_running_containers(
                server_ip, user, project, env
            )
            patterns.extend(running_patterns)
        
        # Deduplicate
        return list(set(patterns))

    @staticmethod
    def _get_patterns_from_running_containers(
        server_ip: str,
        user: str,
        project: str = None,
        env: str = None
    ) -> List[str]:
        """
        Get container patterns from actually running containers.
        
        FALLBACK method when config files not available.
        Uses centralized name parsing.
        
        Args:
            server_ip: Target server IP
            user: user id
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of container patterns from docker ps
        """
        patterns = []
        
        try:
            # Get all running containers
            result = CommandExecuter.run_cmd(
                "docker ps --format '{{.Names}}'",
                server_ip,
                'root'
            )
            
            output = result.stdout if hasattr(result, 'stdout') else str(result)
            
            for line in output.split('\n'):
                container_name = line.strip()
                if not container_name:
                    continue
                
                # Parse using centralized parser
                components = LiveDeploymentQuery._parse_container_name_or_pattern(container_name)
                if not components:
                    continue
                
                # Apply filters
                if user and components['user'] != user:
                    continue
                if project and components['project'] != project:
                    continue
                if env and components['env'] != env:
                    continue
                
                # Add pattern with wildcard
                patterns.append(f"{container_name}*")
                
        except Exception as e:
            log(f"Error getting running containers on {server_ip}: {e}")
        
        return patterns

    @staticmethod
    def _get_patterns_from_deployment_configs(
        server_ip: str,
        user: str,
        project: Optional[str] = None,
        env: Optional[str] = None
    ) -> List[str]:
        """
        Read deployment configs from server to get expected container patterns.
        
        Uses PathResolver to construct proper paths based on server OS.
        
        Args:
            server_ip: Target server IP
            user: user id
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of container patterns from deployment configs
        """
        patterns = []
        
        try:
            # Detect target OS to use correct path format
            target_os = PathResolver.detect_target_os(server_ip)
            
            if project and env:
                # Specific project/env - use PathResolver for config path
                config_path = PathResolver.get_volume_host_path(
                    user, project, env, DEPLOYMENT_CONFIG_SERVICE_NAME, "config", server_ip
                )
                deployment_file = Path(config_path) / DEPLOYMENT_CONFIG_FILENAME
                
                # Read config file from server
                if server_ip == "localhost":
                    if deployment_file.exists():
                        config = json.loads(deployment_file.read_text())
                        patterns.extend(
                            LiveDeploymentQuery._extract_patterns_from_config(
                                config, user
                            )
                        )
                else:
                    # Remote server - read file via SSH
                    try:
                        result = CommandExecuter.run_cmd(
                            f"cat {deployment_file}",
                            server_ip,
                            'root'
                        )
                        output = result.stdout if hasattr(result, 'stdout') else str(result)
                        config = json.loads(output)
                        patterns.extend(
                            LiveDeploymentQuery._extract_patterns_from_config(
                                config, user
                            )
                        )
                    except Exception as e:
                        log(f"Could not read {deployment_file} from {server_ip}: {e}")
            else:
                # All projects/envs for this user
                # Use PathResolver to get base path
                if server_ip == "localhost":
                    base_path = PathResolver.get_volume_host_path(
                        user, "*", "*", None, "config", server_ip
                    )
                    # Remove the wildcards to get actual base
                    base_path = str(base_path).replace("/*", "").replace("\\*", "")
                    user_dir = Path(base_path).parent.parent
                else:
                    # Remote: use standard Linux path
                    user_dir = Path(f"/local/{user}")
                
                if server_ip == "localhost" and user_dir.exists():
                    # Find all deployment.json files locally
                    for config_file in user_dir.rglob(f"config/{DEPLOYMENT_CONFIG_SERVICE_NAME}/{DEPLOYMENT_CONFIG_FILENAME}"):
                        try:
                            config = json.loads(config_file.read_text())
                            patterns.extend(
                                LiveDeploymentQuery._extract_patterns_from_config(
                                    config, user
                                )
                            )
                        except Exception as e:
                            log(f"Error reading {config_file}: {e}")
                            continue
                elif server_ip != "localhost":
                    # Remote: find all deployment.json files via SSH
                    try:
                        result = CommandExecuter.run_cmd(
                            f"find {user_dir} -name '{DEPLOYMENT_CONFIG_FILENAME}' -path '*/config/{DEPLOYMENT_CONFIG_SERVICE_NAME}/*'",
                            server_ip,
                            'root'
                        )
                        output = result.stdout if hasattr(result, 'stdout') else str(result)
                        
                        for config_path in output.split('\n'):
                            config_path = config_path.strip()
                            if not config_path:
                                continue
                            
                            try:
                                result = CommandExecuter.run_cmd(
                                    f"cat {config_path}",
                                    server_ip,
                                    'root'
                                )
                                output = result.stdout if hasattr(result, 'stdout') else str(result)
                                config = json.loads(output)
                                patterns.extend(
                                    LiveDeploymentQuery._extract_patterns_from_config(
                                        config, user
                                    )
                                )
                            except Exception as e:
                                log(f"Error reading {config_path}: {e}")
                                continue
                    except Exception as e:
                        log(f"Error finding deployment configs on {server_ip}: {e}")
                            
        except Exception as e:
            log(f"Error reading server configs: {e}")
        
        return patterns

    @staticmethod
    def _extract_patterns_from_config(config: Dict, user: str) -> List[str]:
        """
        Extract container patterns from deployment config.
        
        Uses ResourceResolver for proper container naming.
        
        Args:
            config: Deployment config dict
            user: user id
            
        Returns:
            List of container patterns
        """
        patterns = []
        
        try:
            project = config['project']['name']
            env = config['env']
            
            for service_name in config.get('services', {}).keys():
                # Use ResourceResolver for container name
                container_name = ResourceResolver.get_container_name(
                    user, project, env, service_name
                )
                patterns.append(f"{container_name}*")
                
        except Exception as e:
            log(f"Error extracting patterns: {e}")
        
        return patterns

    @staticmethod
    def _is_scheduled_pattern(user: str, pattern: str) -> bool:
        """
        Check if container pattern is for a scheduled job.
        
        Reads config file to check if service has 'schedule' field.
        Uses centralized parsing and path resolution.
        
        Args:
            user: user id
            pattern: Container pattern (e.g., "u1_myapp_prod_cleanup_job*")
            
        Returns:
            True if pattern is for scheduled service
        """
        try:
            # Parse pattern using centralized parser
            components = LiveDeploymentQuery._parse_container_name_or_pattern(pattern)
            if not components:
                return False
            
            user_id = components['user']
            project = components['project']
            env = components['env']
            service = components['service']
            
            # Get config path using PathResolver
            config_path = PathResolver.get_volume_host_path(
                user_id, project, env, DEPLOYMENT_CONFIG_SERVICE_NAME, "config", "localhost"
            )
            config_file = Path(config_path) / DEPLOYMENT_CONFIG_FILENAME
            
            if not config_file.exists():
                return False
            
            config = json.loads(config_file.read_text())
            service_config = config.get('services', {}).get(service, {})
            
            return 'schedule' in service_config and service_config['schedule']
            
        except Exception as e:
            log(f"Error checking if scheduled: {e}")
            return False

    @staticmethod
    def _check_recent_scheduled_run(server_ip: str, pattern: str) -> bool:
        """
        Check if scheduled job ran recently and succeeded.
        
        Looks for exited containers matching pattern within last 24h
        with exit code 0-3 (success).
        
        Args:
            server_ip: Target server IP
            pattern: Container pattern
            
        Returns:
            True if recent successful run found
        """
        try:
            # Get exited containers matching pattern
            pattern_filter = pattern.rstrip('*')
            
            cmd = (
                f"docker ps -a "
                f"--filter 'name={pattern_filter}' "
                f"--filter 'status=exited' "
                f"--format '{{{{.Names}}}} {{{{.Status}}}}'"
            )
            
            result = CommandExecuter.run_cmd(cmd, server_ip, 'root')
            output = result.stdout if hasattr(result, 'stdout') else str(result)
            
            if not output.strip():
                return False
            
            # Parse output and check exit codes
            for line in output.split('\n'):
                if not line.strip():
                    continue
                
                # Format: "container_name Exited (0) 2 hours ago"
                if 'Exited' in line:
                    # Extract exit code from "(code)"
                    for part in line.split():
                        if part.startswith('(') and part.endswith(')'):
                            try:
                                exit_code = int(part.strip('()'))
                                if 0 <= exit_code <= 3:  # Success codes
                                    return True
                            except ValueError:
                                continue
            
            return False
            
        except Exception as e:
            log(f"Error checking recent scheduled run: {e}")
            return False

    @staticmethod
    def _get_scheduled_patterns_from_cron(
        server_ip: str,
        user: str,
        project: Optional[str] = None,
        env: Optional[str] = None
    ) -> List[str]:
        """
        Extract container patterns from crontab entries.
        
        Parses crontab to find scheduled docker run commands and
        extracts container name patterns from them.
        
        Args:
            server_ip: Target server IP
            user: SSH user
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of container patterns from cron jobs
        """
        patterns = []
        
        try:
            # Read crontab
            result = CommandExecuter.run_cmd("crontab -l", server_ip, user)
            output = result.stdout if hasattr(result, 'stdout') else str(result)
            
            if not output.strip():
                return patterns
            
            # Parse cron entries for docker run commands
            for line in output.split('\n'):
                if not line.strip() or line.strip().startswith('#'):
                    continue
                
                # Look for docker run commands with --name flag
                if 'docker run' in line and '--name' in line:
                    # Extract container name from --name flag
                    parts = line.split('--name')
                    if len(parts) > 1:
                        # Get the part after --name
                        name_part = parts[1].strip().split()[0]
                        
                        # Parse using centralized parser
                        components = LiveDeploymentQuery._parse_container_name_or_pattern(name_part)
                        if not components:
                            continue
                        
                        # Apply filters
                        if project or env:
                            matches = True
                            if project and components['project'] != project:
                                matches = False
                            if env and components['env'] != env:
                                matches = False
                            if matches:
                                patterns.append(f"{name_part}*")
                        else:
                            patterns.append(f"{name_part}*")
                            
        except Exception as e:
            # crontab -l returns error if no crontab exists - this is OK
            if "no crontab" not in str(e).lower():
                log(f"Error reading crontab on {server_ip}: {e}")
        
        return patterns