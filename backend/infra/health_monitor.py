import time
import requests
import socket
import subprocess
import os
import platform
import traceback
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime, timedelta
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from .server_inventory import ServerInventory
except ImportError:
    from server_inventory import ServerInventory
try:
    from .do_manager import DOManager
except ImportError:
    from do_manager import DOManager
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .auto_scaling_coordinator import AutoScalingCoordinator
except ImportError:
    from auto_scaling_coordinator import AutoScalingCoordinator
try:
    from .live_deployment_query import LiveDeploymentQuery
except ImportError:
    from live_deployment_query import LiveDeploymentQuery
try:
    from . import env_loader
except ImportError:
    import env_loader
try:
    from .path_resolver import PathResolver
except ImportError:
    from path_resolver import PathResolver
try:
    from .deployment_naming import DeploymentNaming
except ImportError:
    from deployment_naming import DeploymentNaming
try:
    from .deployment_port_resolver import DeploymentPortResolver
except ImportError:
    from deployment_port_resolver import DeploymentPortResolver
try:
    from .deployment_state_manager import DeploymentStateManager
except ImportError:
    from deployment_state_manager import DeploymentStateManager
try:
    from .agent_deployer import AgentDeployer
except ImportError:
    from agent_deployer import AgentDeployer
try:
    from .deployment_config import DeploymentConfigurer
except ImportError:
    from deployment_config import DeploymentConfigurer
try:
    from .certificate_manager import CertificateManager
except ImportError:
    from certificate_manager import CertificateManager
try:
    from .do_state_manager import DOStateManager
except ImportError:
    from do_state_manager import DOStateManager
try:
    from .credentials_manager import CredentialsManager
except ImportError:
    from credentials_manager import CredentialsManager
try:
    from .deployment_constants import DEPLOYMENT_CONFIG_SERVICE_NAME, DEPLOYMENT_CONFIG_FILENAME
except ImportError: 
    from deployment_constants import DEPLOYMENT_CONFIG_SERVICE_NAME, DEPLOYMENT_CONFIG_FILENAME


def log(msg):
    Logger.log(msg)


class HealthMonitor:
    """
    Distributed health monitoring with leader-based coordination.
    
    Every server monitors all others. Lowest healthy IP becomes leader
    and coordinates replacements and auto-scaling. Simple, no distributed locks needed.
    """
    
    # Configuration
    MONITOR_INTERVAL = 60  # Check every 60 seconds
    PING_TIMEOUT = 5  # 5 seconds to respond
    HEALTH_CHECK_GRACE = 120  # 2 minutes before considering truly down
    MAX_REPLACEMENT_ATTEMPTS = 3  # Try 3 times before giving up
    MIN_HEALTHY_SERVERS = 1  # Never replace last healthy server
    REPLACEMENT_HISTORY_FILE = Path("config/replacement_history.json")
    AGENT_RESTART_TIMEOUT = 10  # Seconds to wait for agent restart
    
    # Email alert configuration
    ALERT_EMAIL = None  # Set via environment variable ALERT_EMAIL
    
    # Auto-scaling coordinator (singleton)
    _auto_scaling_coordinator = None
    
    @staticmethod
    def get_auto_scaling_coordinator() -> AutoScalingCoordinator:
        """Get or create auto-scaling coordinator singleton"""
        if HealthMonitor._auto_scaling_coordinator is None:
            HealthMonitor._auto_scaling_coordinator = AutoScalingCoordinator()
        return HealthMonitor._auto_scaling_coordinator
    
    # ========================================
    # HTTP AGENT COMMUNICATION
    # ========================================

    @staticmethod
    def get_api_key() -> str:
        """Read health agent API key from local file"""
        api_key_file = Path('/etc/health-agent/api-key')
        if not api_key_file.exists():
            log("Warning: API key file not found at /etc/health-agent/api-key")
            return ""
        return api_key_file.read_text().strip()
    
    @staticmethod
    def agent_request(server_ip: str, method: str, endpoint: str, json_data: dict = None, timeout: int = 30):
        """
        Make authenticated HTTP request to health agent.
        
        Args:
            server_ip: Target server IP
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., "/health")
            json_data: Optional JSON body for POST requests
            timeout: Request timeout in seconds
            
        Returns:
            Response JSON dict
            
        Raises:
            Exception if request fails
        """
        url = f'http://{server_ip}:9999{endpoint}'
        headers = {'X-API-Key': HealthMonitor.get_api_key()}
        
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                json=json_data,
                timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            raise Exception(f"Health agent request timed out: {server_ip}")
        except requests.exceptions.ConnectionError:
            raise Exception(f"Could not connect to health agent: {server_ip}")
        except requests.exceptions.HTTPError as e:
            raise Exception(f"Health agent returned error: {e.response.status_code}")
        except Exception as e:
            raise Exception(f"Health agent request failed: {e}")


    @staticmethod
    def _discover_deployment_contexts() -> List[Dict[str, str]]:
        """
        Discover all user/project/env contexts on this server.
        
        Scans /app/local for deployment.json files at:
        /app/local/{user}/{project}/{env}/config/health_monitor/deployment.json
        
        Returns:
            List of dicts with user/project/env keys
        """
        
        contexts = []
        local_base = Path("/app/local")
        
        if not local_base.exists():
            log("Warning: /app/local not mounted")
            return contexts
        
        # Scan directory structure
        for user_dir in local_base.iterdir():
            if not user_dir.is_dir() or user_dir.name.startswith('.'):
                continue
            
            user = user_dir.name
            
            for project_dir in user_dir.iterdir():
                if not project_dir.is_dir() or project_dir.name.startswith('.'):
                    continue
                
                project = project_dir.name
                
                for env_dir in project_dir.iterdir():
                    if not env_dir.is_dir() or env_dir.name.startswith('.'):
                        continue
                    
                    env = env_dir.name
                    
                    # Check if deployment.json exists
                    deployment_json = (
                        env_dir / "config" / DEPLOYMENT_CONFIG_SERVICE_NAME / 
                        DEPLOYMENT_CONFIG_FILENAME
                    )
                    
                    if deployment_json.exists():
                        contexts.append({
                            'user': user,
                            'project': project,
                            'env': env
                        })
                        log(f"Discovered: {user}/{project}/{env}")
        
        return contexts

    # ========================================
    # CORE MONITORING LOOP
    # ========================================
    
    @staticmethod
    def monitor_and_heal():
        """
        Main monitoring loop - runs on every server.
        
        Discovers all deployment contexts on this server and monitors
        each context's infrastructure independently using context-specific credentials.
        
        Flow:
        1. Discover contexts by scanning /app/local
        2. Group contexts by credentials (DO account)
        3. For each DO account, monitor all its servers
        """
        log("Discovering deployment contexts...")
        contexts = HealthMonitor._discover_deployment_contexts()
        
        if not contexts:
            log("No deployment contexts found")
            return
        
        log(f"Found {len(contexts)} deployment context(s)")
        
        # Group contexts by DO token (same account)
        credentials_map = {}
        
        for context in contexts:
            user = context['user']
            project = context['project']
            env = context['env']
            
            try:
                # Load credentials for this context
                creds = CredentialsManager.get_credentials(user, project, env)
                token = creds['digitalocean_token']
                
                if token not in credentials_map:
                    credentials_map[token] = {
                        'credentials': creds,
                        'contexts': []
                    }
                
                credentials_map[token]['contexts'].append(context)
                
            except Exception as e:
                log(f"Error loading credentials for {user}/{project}/{env}: {e}")
                continue
        
        # Monitor each DO account independently
        for token_key, data in credentials_map.items():
            credentials = data['credentials']
            contexts = data['contexts']
            
            log(f"\n{'='*60}")
            log(f"Monitoring DO account with {len(contexts)} context(s)")
            for ctx in contexts:
                log(f"  - {ctx['user']}/{ctx['project']}/{ctx['env']}")
            log(f"{'='*60}")
            
            # Use the monitoring logic with these credentials
            HealthMonitor._monitor_with_credentials(credentials)


    @staticmethod
    def _monitor_with_credentials(credentials: Dict):
        """
        Execute the monitoring loop with specific credentials.
        
        This contains all the core health monitoring logic:
        1. Collect metrics
        2. Sync with DigitalOcean
        3. Check health of all servers
        4. Determine leader
        5. Leader heals failures
        6. Leader handles auto-scaling
        
        Args:
            credentials: DO credentials for this account
        """
        log("Checking certificates...")
        HealthMonitor._check_my_certificates()

        log("Running health check...")
        
        # STEP 1: Collect metrics from all services (all servers do this)
        coordinator = HealthMonitor.get_auto_scaling_coordinator()
        coordinator.collect_all_metrics()
        
        # STEP 2: Sync inventory with DigitalOcean to get fresh state
        try:
            ServerInventory.sync_with_digitalocean(credentials=credentials)
        except Exception as e:
            log(f"Warning: Could not sync with DigitalOcean: {e}")
        
        # STEP 3: Get all active servers
        all_servers = ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_ACTIVE,
            credentials=credentials
        )
        
        if not all_servers:
            log("No active servers in inventory")
            return
        
        # STEP 4: Check health of each server via agent
        healthy_servers = []
        unhealthy_servers = []
        
        for server in all_servers:
            server_ip = server['ip']
            
            # Ping test (basic reachability)
            if not HealthMonitor.ping_server(server_ip):
                log(f"Server {server_ip} failed ping check")
                unhealthy_servers.append(server)
                continue
            
            # CRITICAL FIX: Check and heal agent FIRST before any other agent-dependent checks
            if not HealthMonitor.check_and_heal_agent(server_ip):
                log(f"Server {server_ip} - agent unavailable after healing attempt")
                unhealthy_servers.append(server)
                continue
            
            # Docker health check via agent (agent now guaranteed to be responding)
            if not HealthMonitor.check_docker_healthy(server_ip):
                log(f"Server {server_ip} failed Docker health check")
                unhealthy_servers.append(server)
                continue
            
            # Container health check via agent
            missing_containers = HealthMonitor.check_service_containers(server)
            if missing_containers:
                log(f"Server {server_ip} missing {len(missing_containers)} containers: {missing_containers}")
                server['missing_containers'] = missing_containers  # Store for later
                unhealthy_servers.append(server)
                continue
            
            # Server is healthy
            healthy_servers.append(server)
        
        log(f"Health check: {len(healthy_servers)} healthy, {len(unhealthy_servers)} unhealthy")
        
        if not healthy_servers:
            log("CRITICAL: No healthy servers! Cannot perform healing.")
            HealthMonitor.send_alert(
                "CRITICAL: All Servers Down",
                "All servers have failed health checks. System is DOWN.\n"
                "Immediate manual intervention required."
            )
            return
        
        # STEP 5: Check if I'm the leader
        if not HealthMonitor.am_i_leader(healthy_servers):
            my_ip = HealthMonitor.get_my_ip()
            leader_ip = sorted([s['ip'] for s in healthy_servers])[0]
            log(f"I am follower ({my_ip}). Leader is {leader_ip}")
            return  # Followers exit here
        
        log(f"I am leader ({HealthMonitor.get_my_ip()})")
        
        # STEP 6: Leader tries to heal unhealthy servers
        if unhealthy_servers:
            # Check minimum healthy servers constraint
            if len(healthy_servers) <= HealthMonitor.MIN_HEALTHY_SERVERS:
                log(f"Cannot heal - would drop below {HealthMonitor.MIN_HEALTHY_SERVERS} healthy servers")
                HealthMonitor.send_alert(
                    "Warning: Cannot Heal Servers",
                    f"Unhealthy servers detected: {[s['ip'] for s in unhealthy_servers]}\n"
                    f"But only {len(healthy_servers)} healthy servers remain.\n"
                    f"Minimum threshold: {HealthMonitor.MIN_HEALTHY_SERVERS}\n"
                    f"Not healing to avoid total system failure."
                )
                return
            
            # Separate servers by failure type
            servers_with_container_failures = []
            servers_completely_failed = []
            
            for server in unhealthy_servers:
                # If server has missing containers, agent is responding
                if 'missing_containers' in server and server['missing_containers']:
                    servers_with_container_failures.append(server)
                else:
                    # Server not responding or Docker down
                    servers_completely_failed.append(server)
            
            # STEP 6a: Try to restart containers on servers that are responding
            if servers_with_container_failures:
                log(f"Attempting to restart containers on {len(servers_with_container_failures)} servers...")
                
                for server in servers_with_container_failures:
                    server_ip = server['ip']
                    missing_containers = server['missing_containers']
                    
                    # CRITICAL FIX: Re-verify agent is still responding before restart attempts
                    # (Agent could have gone down between STEP 4 and now)
                    if not HealthMonitor.check_and_heal_agent(server_ip):
                        log(f"Agent no longer responding on {server_ip}, escalating to server replacement")
                        servers_completely_failed.append(server)
                        continue
                    
                    log(f"Server {server_ip} - attempting to restart {len(missing_containers)} containers")
                    
                    restart_success_count = 0
                    for container_name in missing_containers:
                        if HealthMonitor.restart_container_via_agent(server_ip, container_name):
                            restart_success_count += 1
                    
                    # Check if all containers were restarted
                    if restart_success_count == len(missing_containers):
                        log(f"✓ Successfully restarted all containers on {server_ip}")
                        
                        # Wait a bit for containers to start                        
                        time.sleep(10)
                        
                        # Verify containers are running
                        still_missing = HealthMonitor.check_service_containers(server)
                        if not still_missing:
                            log(f"✓ Server {server_ip} fully recovered")
                            
                            HealthMonitor.send_alert(
                                "Server Recovered",
                                f"Server {server_ip} recovered by restarting containers:\n"
                                f"Restarted: {', '.join(missing_containers)}"
                            )
                        else:
                            log(f"Server {server_ip} still has missing containers: {still_missing}")
                            servers_completely_failed.append(server)
                    else:
                        log(f"Failed to restart all containers on {server_ip} ({restart_success_count}/{len(missing_containers)} successful)")
                        servers_completely_failed.append(server)
            
            # STEP 6b: Replace servers that cannot be recovered
            if servers_completely_failed:
                log(f"Replacing {len(servers_completely_failed)} failed servers...")
                
                for failed_server in servers_completely_failed:
                    server_ip = failed_server['ip']
                    log(f"Detected failed server: {server_ip}")
                    
                    # CRITICAL: Query DO tags to get users on failed server
                    # This works even if server is completely down!
                    users_on_server = DOStateManager.get_users_on_server(
                        server_ip, 
                        credentials=credentials,
                        use_cache=False  # Don't cache during failure recovery
                    )
                    
                    if not users_on_server:
                        log(f"WARNING: No users found on {server_ip} (no service tags in DO)")
                        log(f"Skipping replacement - server had no tagged services")
                        continue
                    
                    log(f"Server {server_ip} has services from {len(users_on_server)} user(s): {users_on_server}")
                    
                    # Get all services for logging
                    services_on_server = DOStateManager.get_services_on_server(
                        server_ip,
                        credentials=credentials,
                        use_cache=False
                    )
                    log(f"Services to restore: {services_on_server}")
                    
                    # Replace server for EACH user separately
                    # Each user's services will be redeployed independently
                    replacement_success = True
                    
                    for user in users_on_server:
                        log(f"Replacing services for user '{user}' on failed server {server_ip}")
                        
                        success = HealthMonitor.replace_server_sequential(
                            failed_server,
                            user=user,
                            services_on_failed_server=services_on_server,  # Pass DO tags data
                            credentials=credentials
                        )
                        
                        if not success:
                            log(f"Failed to replace server for user '{user}'")
                            replacement_success = False
                            # Continue trying other users - don't fail fast
                    
                    if not replacement_success:
                        log(f"Failed to fully replace {server_ip} for all users")
                        # Continue to next server rather than stopping all replacements
                
                # Don't scale after replacements - let system stabilize
                log("Skipping auto-scaling after server replacements (let system stabilize)")
                return
        
        # STEP 7: Leader handles auto-scaling (only if system is stable - no failures)
        coordinator.check_and_scale_all_services()


    @staticmethod
    def check_and_heal_agent(server_ip: str, user: str = "root") -> bool:
        """
        Check if health agent is responding.
        
        NO SSH FALLBACK - relies on systemd auto-restart (Restart=always).
        
        Flow:
        1. Try HTTP ping to agent
        2. If fails, wait 20s for systemd auto-restart
        3. Try HTTP ping again
        4. If still fails, mark server unhealthy (will be replaced)
        
        Args:
            server_ip: Server to check
            user: SSH user (UNUSED - kept for API compatibility)
            
        Returns:
            True if agent is running or recovers via systemd auto-restart
        """
        log(f"Checking health agent on {server_ip}...")
        
        # STEP 1: Try to ping agent via HTTP
        try:
            response = HealthMonitor.agent_request(
                server_ip,
                "GET",
                "/ping",
                timeout=5
            )
            
            if response.get('status') == 'alive':
                log(f"✓ Agent responding on {server_ip}")
                return True
                
        except Exception as e:
            log(f"Agent not responding on {server_ip}: {e}")
        
        # STEP 2: Agent not responding - wait for systemd auto-restart
        log(f"Waiting 20s for systemd auto-restart...")
        time.sleep(20)  # RestartSec=5 + startup time (~15s buffer)
        
        # STEP 3: Try again after waiting for auto-restart
        try:
            response = HealthMonitor.agent_request(
                server_ip,
                "GET",
                "/ping",
                timeout=5
            )
            
            if response.get('status') == 'alive':
                log(f"✓ Agent recovered via systemd auto-restart on {server_ip}")
                return True
                
        except Exception as e:
            log(f"Agent still not responding after auto-restart wait: {e}")
        
        # STEP 4: Agent truly broken - mark server for replacement
        log(f"❌ Agent failed on {server_ip}")
        log(f"Server will be marked unhealthy and replaced (~5 minutes)")
        return False



    # ========================================
    # SERVER HEALTH CHECKS
    # ========================================
    
    @staticmethod
    def get_my_ip() -> str:
        """Get this server's IP address"""
        try:
            # Try to get from environment first (set during deployment)
            my_ip = os.getenv("SERVER_IP")
            if my_ip:
                return my_ip
            
            # Fallback: detect from network
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception as e:
            log(f"Could not determine my IP: {e}")
            return "unknown"
    
    @staticmethod
    def ping_server(ip: str, timeout: int = 5) -> bool:
        """Check if server is reachable via ping"""
        try:            
            param = '-n' if platform.system().lower() == 'windows' else '-c'
            
            result = subprocess.run(
                ['ping', param, '1', '-W' if platform.system().lower() != 'windows' else '-w', 
                 str(timeout * 1000), ip],
                capture_output=True,
                timeout=timeout + 1
            )
            
            return result.returncode == 0
            
        except Exception as e:
            log(f"Ping failed for {ip}: {e}")
            return False
    
    @staticmethod
    def am_i_leader(healthy_servers: List[Dict]) -> bool:
        """
        Check if this server should be the leader.
        Leader is determined by lowest IP address among healthy servers.
        """
        my_ip = HealthMonitor.get_my_ip()
        
        if not healthy_servers:
            return False
        
        # Sort IPs and check if mine is the lowest
        sorted_ips = sorted([s['ip'] for s in healthy_servers])
        return my_ip == sorted_ips[0]
    
    @staticmethod
    def check_docker_healthy(server_ip: str) -> bool:
        """Check if Docker daemon is healthy via agent"""
        try:
            response = HealthMonitor.agent_request(
                server_ip,
                "GET",
                "/docker/health",
                timeout=10
            )
            
            is_healthy = response.get('healthy', False)
            
            if not is_healthy:
                log(f"Docker unhealthy on {server_ip}: {response.get('error', 'unknown')}")
            
            return is_healthy
            
        except Exception as e:
            log(f"Failed to check Docker health on {server_ip}: {e}")
            return False
    
    @staticmethod
    def check_service_containers(server: Dict[str, Any]) -> List[str]:
        """
        Check if expected containers are running on server.
        
        Returns list of missing container names (empty if all healthy).
        """
        server_ip = server['ip']
        
        try:
            # Query running containers from server
            response = HealthMonitor.agent_request(
                server_ip,
                "GET",
                "/containers/list",
                timeout=10
            )
            
            running_containers = set(response.get('containers', []))
            
            # Get expected containers from DO tags
            expected_services = DOStateManager.get_services_on_server(server_ip, use_cache=True)
            
            if not expected_services:
                log(f"No expected services found for {server_ip} in DO tags")
                return []
            
            # Build expected container names
            expected_containers = set()
            for svc in expected_services:
                container_name = DeploymentNaming.get_container_name(
                    svc['project'], svc['env'], svc['service']
                )
                expected_containers.add(container_name)
            
            # Find missing
            missing = expected_containers - running_containers
            
            if missing:
                log(f"Missing containers on {server_ip}: {missing}")
            
            return list(missing)
            
        except Exception as e:
            log(f"Failed to check containers on {server_ip}: {e}")
            # Assume all missing if we can't check
            return []
    
    @staticmethod
    def restart_container_via_agent(server_ip: str, container_name: str) -> bool:
        """Restart a container via health agent"""
        try:
            log(f"Restarting container {container_name} on {server_ip}...")
            
            response = HealthMonitor.agent_request(
                server_ip,
                "POST",
                "/containers/restart",
                json_data={'name': container_name},
                timeout=30
            )
            
            if response.get('status') == 'success':
                log(f"✓ Container {container_name} restarted successfully")
                return True
            else:
                log(f"✗ Failed to restart {container_name}: {response.get('message', 'unknown')}")
                return False
                
        except Exception as e:
            log(f"Error restarting {container_name} on {server_ip}: {e}")
            return False

    # ========================================
    # SERVER REPLACEMENT
    # ========================================

    @staticmethod
    def _build_container_config(service: Dict[str, Any], server_ip: str, user: str) -> Dict[str, Any]:
        """
        Build container configuration for agent deployment.
        
        Args:
            service: Service info dict with name, project, env, config
            server_ip: Target server IP
            user: User ID
            
        Returns:
            Container config dict for agent
        """
        project = service['project']
        env = service['env']
        service_name = service['name']
        config = service['config']
        
        # Container name
        container_name = DeploymentNaming.get_container_name(project, env, service_name)
        
        # Image
        image = config.get('image')
        if not image:
            # Use docker hub user + project + service for custom images
            docker_hub_user = config.get('docker_hub_user', 'local')
            version = config.get('version', 'latest')
            image = f"{docker_hub_user}/{project}_{service_name}:{version}"
        
        # Ports
        ports = {}
        if config.get('port'):
            # Get host port from hash            
            host_port = DeploymentPortResolver.get_host_port(user, project, env, service_name)
            container_port = config['port']
            ports[str(host_port)] = str(container_port)
        
        # Volumes
        volumes = PathResolver.generate_all_volume_mounts(user, project, env, service_name, server_ip)
        
        # Environment variables
        env_vars = config.get('env_vars', {})
        
        # Network
        network = DeploymentNaming.get_network_name(project, env)
        
        # Restart policy
        restart_policy = "unless-stopped" if config.get('restart', True) else "no"
        
        return {
            'name': container_name,
            'image': image,
            'ports': ports,
            'volumes': volumes,
            'env_vars': env_vars,
            'network': network,
            'restart_policy': restart_policy
        }

    @staticmethod
    def replace_server_sequential(
    failed_server: Dict[str, Any], 
    user: str, 
    services_on_failed_server: List[Dict[str, str]] = None,
    credentials: Dict = None
) -> bool:
        """
        Replace a failed server using health agent (no SSH needed).
        
        NEW: Uses shortfall detection to deploy all needed services, not just what failed.
        NEW: Coordinates with auto-scaling via infrastructure lock.
        
        Process:
        1. Check and acquire infrastructure lock
        2. Calculate what SHOULD be deployed (shortfall detection)
        3. Create replacement with same specs (DO API)
        4. Wait for it to become active
        5. Push config/secrets via agent HTTP
        6. Deploy services via agent HTTP
        7. Health check via agent HTTP
        8. If healthy: destroy old, update state
        9. Release infrastructure lock
        
        Args:
            failed_server: Server info dict with ip, zone, cpu, memory, droplet_id
            user: User ID for this infrastructure
            services_on_failed_server: Services from DO tags (avoids querying dead server)
            credentials: Optional credentials dict
            
        Returns:
            True if replacement successful
        """        
        log(f"Replacing failed server {failed_server['ip']}")
        Logger.start()
        
        # NEW: Check if infrastructure is already being modified
        if DOManager.is_infrastructure_locked(credentials):
            log("Infrastructure modification in progress (healing or auto-scaling), skipping replacement")
            Logger.end()
            return False
        
        # NEW: Acquire infrastructure lock
        leader_ip = HealthMonitor.get_my_ip()
        if not DOManager.acquire_infrastructure_lock(leader_ip, credentials):
            log("Failed to acquire infrastructure lock")
            Logger.end()
            return False
        
        try:
            # Get services from DO tags (passed from STEP 6b or fallback query)
            if services_on_failed_server is None:
                # Fallback: query DO tags if not provided
                log("Services not provided, querying DO tags...")
                services_on_failed_server = DOStateManager.get_services_on_server(
                    failed_server['ip'],
                    credentials=credentials,
                    use_cache=False
                )
            
            # Filter to only this user's services
            user_services = [s for s in services_on_failed_server if s['user'] == user]
            
            if not user_services:
                log(f"No services found for user '{user}' on failed server")
                Logger.end()
                DOManager.release_infrastructure_lock(leader_ip, credentials)
                return True
            
            log(f"User '{user}' had {len(user_services)} services on failed server:")
            for svc in user_services:
                log(f"  - {svc['project']}:{svc['env']}:{svc['service']}")
            
            # Get project/env from first service
            project = user_services[0]['project']
            env = user_services[0]['env']
            
            # NEW: SHORTFALL DETECTION
            # Instead of just deploying what failed, calculate what SHOULD be deployed
            log(f"Calculating service shortfall for {project}/{env}...")
            
            configurer = DeploymentConfigurer(user, project)
            all_services = configurer.get_services(env)
            
            services_to_deploy = []
            for service_name, service_config in all_services.items():
                required = service_config.get('servers_count', 1)
                
                # Query live infrastructure for current count
                current_servers = LiveDeploymentQuery.get_servers_running_service(
                    user, project, env, service_name, credentials
                )
                current_count = len(current_servers)
                
                shortfall = required - current_count
                
                if shortfall > 0:
                    log(f"  {service_name}: required={required}, current={current_count}, shortfall={shortfall}")
                    services_to_deploy.append({
                        'name': service_name,
                        'project': project,
                        'env': env,
                        'config': service_config
                    })
                else:
                    log(f"  {service_name}: OK (required={required}, current={current_count})")
            
            if not services_to_deploy:
                log("✓ No service shortfall detected - all services at required count")
                Logger.end()
                return True
            
            log(f"Will deploy {len(services_to_deploy)} services to new server: {[s['name'] for s in services_to_deploy]}")
            
            # Attempt replacement with retry logic
            for attempt in range(1, HealthMonitor.MAX_REPLACEMENT_ATTEMPTS + 1):
                log(f"Replacement attempt {attempt}/{HealthMonitor.MAX_REPLACEMENT_ATTEMPTS}")
                
                try:
                    # STEP 1: Create replacement with same specs (DO API)
                    log("Creating replacement server via DigitalOcean API...")
                    new_droplets = DOManager.create_servers(
                        count=1,
                        region=failed_server['zone'],
                        cpu=failed_server['cpu'],
                        memory=failed_server['memory'],
                        tags=["deployer", f"replacement_for:{failed_server['ip']}"],
                        credentials=credentials
                    )
                    
                    if not new_droplets:
                        log("Failed to create replacement droplet")
                        continue
                    
                    new_server = new_droplets[0]
                    log(f"✓ Created replacement: {new_server['ip']}")
                    
                    # Add to inventory as RESERVE (not active yet)
                    ServerInventory.update_server_status([new_server['ip']], ServerInventory.STATUS_RESERVE, credentials=credentials)
                    
                    # STEP 2: Wait for server to be ready
                    log("Waiting for server to boot...")
                    time.sleep(30)  # Give it time to fully boot
                    
                    # Verify agent is responding
                    agent_ready = False
                    for i in range(10):  # Try for 50 seconds
                        try:
                            response = HealthMonitor.agent_request(
                                new_server['ip'],
                                "GET",
                                "/ping",
                                timeout=5
                            )
                            if response.get('status') == 'alive':
                                log(f"✓ Health agent responding on {new_server['ip']}")
                                agent_ready = True
                                break
                        except:
                            log(f"Waiting for agent to respond... ({i+1}/10)")
                            time.sleep(5)
                    
                    if not agent_ready:
                        log(f"Health agent not responding on {new_server['ip']}")
                        DOManager.destroy_droplet(new_server['droplet_id'], credentials=credentials)
                        ServerInventory.release_servers([new_server['ip']], destroy=False, credentials=credentials)
                        continue
                    
                    # STEP 3: Push config/secrets to new server via agent
                    log(f"Pushing config/secrets to {new_server['ip']}...")
                    
                    push_success = AgentDeployer.push_files_to_server(
                        new_server['ip'],
                        user,
                        project,
                        env,
                        directories=['config', 'secrets', 'files']
                    )
                    
                    if not push_success:
                        log("Failed to push files to replacement server")
                        DOManager.destroy_droplet(new_server['droplet_id'], credentials=credentials)
                        ServerInventory.release_servers([new_server['ip']], destroy=False, credentials=credentials)
                        continue
                    
                    log("✓ Config/secrets pushed")
                    
                    # STEP 4: Deploy services to new server via agent
                    log(f"Deploying {len(services_to_deploy)} services via agent...")
                    
                    # Group by startup_order
                    services_by_order = {}
                    for service in services_to_deploy:
                        order = service['config'].get('startup_order', 0)
                        if order not in services_by_order:
                            services_by_order[order] = []
                        services_by_order[order].append(service)
                    
                    # Deploy in order
                    deployment_success = True
                    for startup_order in sorted(services_by_order.keys()):
                        log(f"Deploying services with startup_order={startup_order}...")
                        
                        for service in services_by_order[startup_order]:
                            success = AgentDeployer.deploy_container(
                                new_server['ip'],
                                HealthMonitor._build_container_config(service, new_server['ip'], user)
                            )
                            
                            if not success:
                                log(f"Failed to deploy {service['name']}")
                                deployment_success = False
                                break
                            
                            # Verify container started
                            container_name = DeploymentNaming.get_container_name(
                                service['project'], service['env'], service['name']
                            )
                            if not AgentDeployer.verify_container_running(new_server['ip'], container_name):
                                log(f"Container {container_name} did not start properly")
                                deployment_success = False
                                break
                        
                        if not deployment_success:
                            break
                        
                        # Wait between startup orders
                        if startup_order != max(services_by_order.keys()):
                            log("Waiting 10 seconds before next startup_order group...")
                            time.sleep(10)
                    
                    if not deployment_success:
                        log("Deployment failed")
                        DOManager.destroy_droplet(new_server['droplet_id'], credentials=credentials)
                        ServerInventory.release_servers([new_server['ip']], destroy=False, credentials=credentials)
                        continue
                    
                    log("✓ All services deployed successfully")
                    
                   
                    # STEP 5: Health check replacement
                    log("Performing health check on replacement...")
                    time.sleep(10)  # Wait for services to fully start
                    
                    if HealthMonitor.ping_server(new_server['ip']):
                        if HealthMonitor.check_docker_healthy(new_server['ip']):
                            missing = HealthMonitor.check_service_containers(new_server)
                            
                            if not missing:
                                log("✓ Replacement is healthy!")
                                
                                # Mark as ACTIVE
                                ServerInventory.update_server_status([new_server['ip']], ServerInventory.STATUS_ACTIVE, credentials=credentials)
                                
                                # Update DO tags for each service
                                for service in services_to_deploy:
                                    try:
                                        DOManager.add_droplet_tag(
                                            new_server['droplet_id'],
                                            f"service:{user}:{service['project']}:{service['env']}:{service['name']}",
                                            credentials=credentials
                                        )
                                        
                                    except Exception as e:
                                        log(f"Warning: Could not add tag for {service['name']}: {e}")
                                    DeploymentStateManager.add_server_to_service(
                                        user,
                                        service['project'],
                                        service['env'],
                                        service['name'],
                                        new_server['ip']
                                    )
                                
                                # Remove failed server from all services
                                DeploymentStateManager.remove_server_from_all_services(user, failed_server['ip'])
                                
                                # STEP 6: Destroy failed server
                                log(f"Destroying failed server {failed_server['ip']}...")
                                DOManager.destroy_droplet(failed_server['droplet_id'], credentials=credentials)
                                ServerInventory.release_servers([failed_server['ip']], destroy=False, credentials=credentials)
                                
                                HealthMonitor.record_replacement_attempt(
                                    failed_server['ip'],
                                    True,
                                    f"Replaced with {new_server['ip']}"
                                )
                                
                                Logger.end()
                                log(f"✓ Successfully replaced {failed_server['ip']} with {new_server['ip']}")
                                
                                HealthMonitor.send_alert(
                                    "Server Replacement Successful",
                                    f"Failed server {failed_server['ip']} replaced with {new_server['ip']}\n"
                                    f"Services deployed: {len(services_to_deploy)}\n"
                                    f"Services: {', '.join([s['name'] for s in services_to_deploy])}\n"
                                    f"All autonomous - no manual intervention required."
                                )
                                
                                return True
                            else:
                                log(f"Replacement {new_server['ip']} missing containers: {missing}")
                        else:
                            log(f"Replacement {new_server['ip']} failed Docker health check")
                    else:
                        log(f"Replacement {new_server['ip']} failed ping check")
                    
                    # Unhealthy - destroy and retry
                    log("Replacement unhealthy, destroying and retrying...")
                    DOManager.destroy_droplet(new_server['droplet_id'], credentials=credentials)
                    ServerInventory.release_servers([new_server['ip']], destroy=False, credentials=credentials)
                    
                    if attempt == HealthMonitor.MAX_REPLACEMENT_ATTEMPTS:
                        HealthMonitor.record_replacement_attempt(
                            failed_server['ip'],
                            False,
                            f"Failed after {attempt} attempts"
                        )
                
                except Exception as e:
                    log(f"Replacement attempt {attempt} failed: {e}")                
                    traceback.print_exc()
                    
                    if attempt == HealthMonitor.MAX_REPLACEMENT_ATTEMPTS:
                        HealthMonitor.record_replacement_attempt(
                            failed_server['ip'],
                            False,
                            f"Exception: {str(e)}"
                        )
            
            Logger.end()
            log(f"❌ Failed to replace {failed_server['ip']} after {HealthMonitor.MAX_REPLACEMENT_ATTEMPTS} attempts")
            
            HealthMonitor.send_alert(
                "Server Replacement FAILED",
                f"CRITICAL: Failed to replace server {failed_server['ip']}\n"
                f"Services affected: {len(services_to_deploy)}\n"
                f"Manual intervention required."
            )
            
            return False
        
        finally:
            # ALWAYS release lock, even if replacement failed
            DOManager.release_infrastructure_lock(leader_ip, credentials)
            log("Infrastructure lock released")





    # ========================================
    # UTILITIES
    # ========================================

    @staticmethod
    def _check_my_certificates():
        """
        Check and renew certificates on THIS server.
        
        Runs on EVERY server independently (not just leader).
        Each server manages its own certificates.
        Rate limited to run once per hour.
        """       
        try:
            # Rate limiting - only check once per hour
            last_check_file = Path("/tmp/cert_last_check")
            
            if last_check_file.exists():
                last_check = datetime.fromtimestamp(last_check_file.stat().st_mtime)
                minutes_since_check = (datetime.now() - last_check).total_seconds() / 60
                
                if minutes_since_check < 60:  # Check every 60 minutes
                    return  # Not time to check yet
            
            # Update last check time
            last_check_file.touch()
            
            log("🔒 Checking MY SSL certificates...")
            
            # Check and renew certificates on THIS server
            # (CertificateManager reads local files and executes certbot locally)
            results = CertificateManager.check_and_renew_all()
            
            # Send alerts for failures
            failures = [domain for domain, success in results.items() if success is False]
            
            if failures:
                HealthMonitor.send_alert(
                    "Certificate Renewal Failed",
                    f"Failed to renew certificates on this server: {', '.join(failures)}"
                )
                
        except Exception as e:
            log(f"Error checking certificates: {e}")

    @staticmethod
    def record_replacement_attempt(server_ip: str, success: bool, reason: str = ""):
        """Record replacement attempt for history/debugging"""        
        history = []
        if HealthMonitor.REPLACEMENT_HISTORY_FILE.exists():
            try:
                history = json.loads(HealthMonitor.REPLACEMENT_HISTORY_FILE.read_text())
            except:
                history = []
        
        history.append({
            "timestamp": datetime.now().isoformat(),
            "server_ip": server_ip,
            "success": success,
            "reason": reason,
            "replaced_by": HealthMonitor.get_my_ip()
        })
        
        history = history[-100:]
        
        HealthMonitor.REPLACEMENT_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HealthMonitor.REPLACEMENT_HISTORY_FILE.write_text(json.dumps(history, indent=2))
    
    @staticmethod
    def send_alert(subject: str, message: str):
        """Send email alert about critical issues"""
        try:
            smtp_host = "smtp.gmail.com"
            smtp_port = 587
            email = os.getenv("ADMIN_EMAIL", "robinworld.contact@gmail.com")
            password = os.getenv("GMAIL_APP_PASSWORD")

            if not password:
                log("No GMAIL_APP_PASSWORD configured, skipping alert")
                return
            
            msg = MIMEMultipart()
            msg['From'] = email
            msg['To'] = email
            msg['Subject'] = f"[Health Monitor] {subject}"
            
            msg.attach(MIMEText(message, 'plain'))
            
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(email, password)
                server.send_message(msg)
            
            log(f"Alert sent to {email}")
            
        except Exception as e:
            log(f"Failed to send alert email: {e}")
    
    @staticmethod
    def start_monitoring_daemon():
        """
        Start monitoring daemon that runs forever.
        Used when running as a standalone service.
        """
        log(f"Starting health monitor daemon")
        log(f"My IP: {HealthMonitor.get_my_ip()}")
        log(f"Check interval: {HealthMonitor.MONITOR_INTERVAL}s")
        
        while True:
            try:
                HealthMonitor.monitor_and_heal()
            except Exception as e:
                log(f"Monitor error: {e}")
            
            time.sleep(HealthMonitor.MONITOR_INTERVAL)


def main():
    """CLI interface for health monitoring"""
    HealthMonitor.monitor_and_heal()


if __name__ == "__main__":
    main()