"""
LiveDeploymentQuery - Query actual running infrastructure as source of truth

This module provides pure live queries without circular imports.
It queries:
- Running containers (docker ps)
- Nginx configs (/etc/nginx/stream.d/)
- Deployment configs (/local/{user}/{project}/{env}/config/health_monitor/deployment.json)
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
    from .deployment_naming import DeploymentNaming
except ImportError:
    from deployment_naming import DeploymentNaming


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
        env: str = None,
        user: str = "root"
    ) -> List[str]:
        """
        Get containers that SHOULD be running on server.
        
        Uses multiple sources for robustness:
        1. /local/{user}/{project}/{env}/config/health_monitor/deployment.json (PRIMARY)
        2. /etc/nginx/stream.d/*.conf (CROSS-VALIDATION - nginx configs)
        3. crontab -l (VERIFICATION - scheduled jobs)
        
        Config files are synced via standard DeploymentSyncer.push_directory(),
        ensuring consistency with rest of system.
        
        Args:
            server_ip: Target server IP (not used for local reads)
            project: Optional filter by project
            env: Optional filter by environment
            user: user id for config files (default: "root")
            
        Returns:
            List of container name patterns (e.g., ["u1_myapp_prod_postgres*", ...])
        """
        patterns = []
        
        # PRIMARY SOURCE: Read deployment config files
        config_patterns = LiveDeploymentQuery._read_server_configs(
            user, project, env
        )
        patterns.extend(config_patterns)
        
        # CROSS-VALIDATION: Parse nginx configs
        nginx_containers = NginxConfigParser.get_expected_containers_on_server(
            server_ip, project, env
        )
        
        # Add nginx containers not already in patterns
        for container in nginx_containers:
            pattern = container if container.endswith('*') else f"{container}*"
            if pattern not in patterns:
                patterns.append(pattern)
        
        # VERIFICATION: Check crontab for scheduled jobs
        cron_patterns = LiveDeploymentQuery._get_scheduled_patterns_from_cron(
            server_ip, user, project, env
        )
        patterns.extend(cron_patterns)
        
        return patterns

    @staticmethod
    def compare_expected_vs_actual(
        server_ip: str,
        project: str = None,
        env: str = None,
        user: str = "root"
    ) -> Dict[str, Any]:
        """
        Compare expected containers vs actual running containers.
        
        Intelligently handles:
        - Long-running services (must be running)
        - Scheduled jobs (OK if exited with code 0-3 within 24h)
        - Worker jobs (checked via recent runs)
        
        Args:
            server_ip: Target server IP
            project: Optional filter by project
            env: Optional filter by environment
            user: user id for config files
            
        Returns:
            Dict with:
                - expected: List of expected patterns
                - actual: List of actual container names
                - missing: List of patterns not found (CRITICAL for long-running services)
                - unexpected: List of containers not in config
                - scheduled_ok: List of scheduled jobs that ran successfully
        """
        # Get what SHOULD be running
        expected_patterns = LiveDeploymentQuery.get_expected_containers_on_server(
            server_ip, project, env, user
        )
        
        # Get what IS running
        try:
            cmd = "docker ps --format '{{.Names}}'"
            result = CommandExecuter.run_cmd(cmd, server_ip, 'root')
            output = result.stdout if hasattr(result, 'stdout') else str(result)
            actual_containers = [
                c.strip() 
                for c in output.split('\n') 
                if c.strip()
            ]
        except Exception as e:
            log(f"Error querying containers on {server_ip}: {e}")
            actual_containers = []
        
        # Apply filters if specified
        if project or env:
            filtered_actual = []
            for container in actual_containers:
                parts = container.split('_')
                if len(parts) >= 3:
                    matches_filter = True
                    if project and parts[1] != project:
                        matches_filter = False
                    if env and parts[2] != env:
                        matches_filter = False
                    if matches_filter:
                        filtered_actual.append(container)
            actual_containers = filtered_actual
        
        # Find missing containers (expected but not running)
        missing = []
        scheduled_ok = []
        
        for pattern in expected_patterns:
            # Check if any running container matches this pattern
            pattern_matched = any(
                fnmatch.fnmatch(container, pattern)
                for container in actual_containers
            )
            
            if not pattern_matched:
                # Not running - check if it's a scheduled job
                if LiveDeploymentQuery._is_scheduled_pattern(user, pattern):
                    # Check if it ran recently and succeeded
                    if LiveDeploymentQuery._check_recent_scheduled_run(server_ip, pattern):
                        scheduled_ok.append(pattern)
                    else:
                        missing.append(pattern)
                else:
                    # Long-running service not running - CRITICAL
                    missing.append(pattern)
        
        # Find unexpected containers (running but not in config)
        unexpected = []
        for container in actual_containers:
            matched = any(
                fnmatch.fnmatch(container, pattern)
                for pattern in expected_patterns
            )
            if not matched:
                unexpected.append(container)
        
        return {
            "expected": expected_patterns,
            "actual": actual_containers,
            "missing": missing,
            "unexpected": unexpected,
            "scheduled_ok": scheduled_ok
        }

    @staticmethod
    def get_all_deployments(
        project: str = None,
        env: str = None
    ) -> Dict[str, Any]:
        """
        Get all deployments across all servers.
        
        Args:
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            Dict with:
                - services: Dict[service_name -> List[server_ips]]
                - total_servers: Count of servers with containers
                - total_containers: Count of total containers
                - servers: List of all server IPs
        """
        all_servers = ServerInventory.list_all_servers()
        
        containers_by_server = {}
        services = {}
        
        for server in all_servers:
            server_ip = server['ip']
            
            try:
                # Get all containers on this server
                result = CommandExecuter.run_cmd(
                    "docker ps --format '{{.Names}}'",
                    server_ip,
                    'root'
                )
                
                if hasattr(result, 'stdout'):
                    output = result.stdout.strip()
                else:
                    output = str(result).strip()
                
                containers = [c.strip() for c in output.split('\n') if c.strip()]
                
                # Apply filters
                if project or env:
                    filtered = []
                    for container in containers:
                        parts = container.split('_')
                        if len(parts) >= 3:
                            matches = True
                            if project and parts[1] != project:
                                matches = False
                            if env and parts[2] != env:
                                matches = False
                            if matches:
                                filtered.append(container)
                    containers = filtered
                
                if containers:
                    containers_by_server[server_ip] = containers
                
                # Group by service
                for container_name in containers:
                    parts = container_name.split('_')
                    if len(parts) >= 3:
                        svc_name = parts[2]  # Service is 3rd part
                        
                        if svc_name not in services:
                            services[svc_name] = []
                        
                        if server_ip not in services[svc_name]:
                            services[svc_name].append(server_ip)
            
            except Exception as e:
                log(f"Could not query containers on {server_ip}: {e}")
                continue
        
        total_containers = sum(len(containers) for containers in containers_by_server.values())
        
        return {
            "services": services,
            "total_servers": len(containers_by_server),
            "total_containers": total_containers,
            "servers": list(containers_by_server.keys())
        }
    

    @staticmethod
    def _read_server_configs(
        user: str,
        project: str = None,
        env: str = None
    ) -> List[str]:
        """
        Read deployment configs to get expected container patterns.
        
        Reads from /app/local (mounted in health monitor container) or
        from local filesystem (when called from bastion).
        
        Uses PathResolver to get correct paths (no hardcoded paths).
        
        Args:
            user: user id (e.g. "u1")
            project: Optional filter by project
            env: Optional filter by environment
            
        Returns:
            List of container patterns (e.g., ["u1_myapp_prod_postgres*", ...])
        """
        patterns = []
        
        try:
            # Determine base path using PathResolver
            # In container: /app/local exists and takes precedence
            # On bastion: use PathResolver to get the correct local path
            if Path("/app/local").exists():
                # Running inside health monitor container
                base = Path("/app/local")
            else:
                # Running on bastion - use PathResolver
                # Get config path for any dummy service to extract base
                sample_path = PathResolver.get_volume_host_path(
                    user, "dummy_project", "dummy_env", "health_monitor", "config", "localhost"
                )
                # Path structure: /local/{user}/{project}/{env}/config/health_monitor
                # Go up 4 levels to get /local/{user}
                base = Path(sample_path).parent.parent.parent.parent
            
            # Build search path
            if project and env:
                # Specific project/env - use PathResolver
                config_path = Path(PathResolver.get_volume_host_path(
                    user, project, env, "health_monitor", "config", "localhost"
                )) / "deployment.json"
                
                if config_path.exists():
                    config = json.loads(config_path.read_text())
                    patterns.extend(
                        LiveDeploymentQuery._extract_patterns_from_config(
                            config, user
                        )
                    )
            else:
                # All projects/envs for this user
                user_dir = base / user
                
                if user_dir.exists():
                    # Find all deployment.json files
                    for config_file in user_dir.rglob("config/health_monitor/deployment.json"):
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
                            
        except Exception as e:
            log(f"Error reading server configs: {e}")
        
        return patterns

    @staticmethod
    def _extract_patterns_from_config(config: Dict, user: str) -> List[str]:
        """
        Extract container patterns from deployment config.
        
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
                pattern = DeploymentNaming.get_container_name_pattern(
                    user, project, env, service_name
                )
                patterns.append(pattern)
                
        except Exception as e:
            log(f"Error extracting patterns: {e}")
        
        return patterns

    @staticmethod
    def _is_scheduled_pattern(user: str, pattern: str) -> bool:
        """
        Check if container pattern is for a scheduled job.
        
        Reads config file to check if service has 'schedule' field.
        Uses PathResolver for correct paths.
        
        Args:
            user: user id
            pattern: Container pattern (e.g., "u1_myapp_prod_cleanup_job*")
            
        Returns:
            True if pattern is for scheduled service
        """
        try:
            # Parse pattern to extract project/env/service
            # Pattern format: {user}_{project}_{env}_{service}*
            parts = pattern.rstrip('*').split('_')
            if len(parts) < 4:
                return False
            
            user_id = parts[0]
            project = parts[1]
            env = parts[2]
            service = '_'.join(parts[3:])  # Service might have underscores
            
            # Get config path using PathResolver
            if Path("/app/local").exists():
                # In container - construct path directly
                config_path = Path(f"/app/local/{user_id}/{project}/{env}/config/health_monitor/deployment.json")
            else:
                # On bastion - use PathResolver
                config_dir = PathResolver.get_volume_host_path(
                    user_id, project, env, "health_monitor", "config", "localhost"
                )
                config_path = Path(config_dir) / "deployment.json"
            
            if not config_path.exists():
                return False
            
            config = json.loads(config_path.read_text())
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
        project: str = None,
        env: str = None
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
                        
                        # Apply filters
                        if project or env:
                            name_parts = name_part.split('_')
                            if len(name_parts) >= 3:
                                matches = True
                                if project and name_parts[1] != project:
                                    matches = False
                                if env and name_parts[2] != env:
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