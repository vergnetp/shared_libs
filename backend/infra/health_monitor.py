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
        
    # ========================================
    # CORE MONITORING LOOP
    # ========================================
    
    @staticmethod
    def monitor_and_heal():
        """
        Main monitoring loop - runs on every server.
        Only leader takes action.
        
        Flow:
        1. Collect metrics (all servers)
        2. Check health via agent HTTP (all servers)
        3. Determine leader (all servers)
        4. Leader tries to restart failed containers (leader only)
        5. Leader replaces unreachable servers (leader only)
        6. Leader handles auto-scaling (leader only, if system is stable)
        """
        log("Checking certificates...")
        HealthMonitor._check_my_certificates()

        log("Running health check...")
        
        # STEP 1: Collect metrics from all services (all servers do this)
        coordinator = HealthMonitor.get_auto_scaling_coordinator()
        coordinator.collect_all_metrics()
        
        # STEP 2: Sync inventory with DigitalOcean to get fresh state
        try:
            ServerInventory.sync_with_digitalocean()
        except Exception as e:
            log(f"Warning: Could not sync with DigitalOcean: {e}")
        
        # STEP 3: Get all active servers
        all_servers = ServerInventory.get_servers(deployment_status=ServerInventory.STATUS_ACTIVE)
        
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
            
            # Docker health check via agent
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
                    log(f"Detected failed server: {failed_server['ip']}")
                    
                    success = HealthMonitor.replace_server_sequential(failed_server)
                    
                    if not success:
                        log(f"Failed to replace {failed_server['ip']} - stopping replacements")
                        break
                
                # Don't scale after replacements - let system stabilize
                log("Skipping auto-scaling after server replacements (let system stabilize)")
                return
        
        # STEP 7: Leader handles auto-scaling (only if system is stable - no failures)
        coordinator.check_and_scale_all_services()



    @staticmethod
    def check_and_heal_agent(server_ip: str, user: str = "root") -> bool:
        """
        Check if health agent is running and try to restart if needed.
        
        This prevents false positives where the server is healthy but
        only the health agent service is down.
        
        Args:
            server_ip: Server to check
            user: SSH user
            
        Returns:
            True if agent is running (or was successfully restarted)
        """
        log(f"Checking health agent on {server_ip}...")
        
        try:
            # 1. Try to ping agent
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
            
            # 2. Agent not responding - check if service exists
            log(f"Checking if agent service exists on {server_ip}...")
            
            try:
                from .execute_cmd import CommandExecuter
            except ImportError:
                from execute_cmd import CommandExecuter
            
            service_check = CommandExecuter.run_cmd(
                "systemctl list-units --type=service | grep -q 'health-agent' && echo 'EXISTS' || echo 'MISSING'",
                server_ip, user
            )
            
            if 'MISSING' in str(service_check):
                log(f"Health agent service not installed on {server_ip}")
                return False
            
            # 3. Service exists - check its status
            log(f"Checking agent service status on {server_ip}...")
            
            status_check = CommandExecuter.run_cmd(
                "systemctl is-active health-agent || echo 'INACTIVE'",
                server_ip, user
            )
            
            if 'inactive' in str(status_check).lower() or 'failed' in str(status_check).lower():
                log(f"Agent service is down on {server_ip}, attempting restart...")
                
                # 4. Try to restart service
                try:
                    CommandExecuter.run_cmd(
                        "systemctl restart health-agent",
                        server_ip, user
                    )
                    
                    log(f"Waiting {HealthMonitor.AGENT_RESTART_TIMEOUT}s for agent to start...")
                    time.sleep(HealthMonitor.AGENT_RESTART_TIMEOUT)
                    
                    # 5. Verify agent is now responding
                    response = HealthMonitor.agent_request(
                        server_ip,
                        "GET",
                        "/ping",
                        timeout=5
                    )
                    
                    if response.get('status') == 'alive':
                        log(f"✓ Agent successfully restarted on {server_ip}")
                        
                        HealthMonitor.send_alert(
                            "Health Agent Auto-Healed",
                            f"Health agent on {server_ip} was down and has been automatically restarted.\n"
                            f"Server is healthy - no replacement needed."
                        )
                        
                        return True
                    else:
                        log(f"Agent restart failed - still not responding on {server_ip}")
                        return False
                        
                except Exception as e:
                    log(f"Failed to restart agent on {server_ip}: {e}")
                    return False
            else:
                log(f"Agent service is running but not responding on {server_ip}")
                return False
                
        except Exception as e:
            log(f"Error checking agent on {server_ip}: {e}")
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
    def check_docker_healthy(server_ip: str) -> bool:
        """
        Check if Docker is healthy on server via agent.
        
        UPDATED: Now includes agent self-healing.
        
        Args:
            server_ip: Target server IP
            
        Returns:
            True if Docker is healthy (and agent is working)
        """
        try:
            # NEW: First check/heal the agent itself
            if not HealthMonitor.check_and_heal_agent(server_ip):
                log(f"Agent unavailable on {server_ip} - cannot check Docker")
                return False
            
            # Original check continues as before
            response = HealthMonitor.agent_request(
                server_ip,
                "GET",
                "/docker/health",
                timeout=10
            )
            
            return response.get('status') == 'healthy'
            
        except Exception as e:
            log(f"Docker health check failed for {server_ip}: {e}")
            return False
    
    @staticmethod
    def check_service_containers(server: Dict[str, Any]) -> List[str]:
        """
        Check if all expected service containers are running on a server.
        
        Returns list of missing container names.
        """      
        ip = server['ip']
        
        # Use live query to compare expected vs actual
        diff = LiveDeploymentQuery.compare_expected_vs_actual(ip)
        
        # Log details
        if diff['missing']:
            for container_name in diff['missing']:
                log(f"Missing container on {ip}: {container_name}")
        
        return diff['missing']

    @staticmethod
    def restart_container_via_agent(server_ip: str, container_name: str) -> bool:
        """
        Restart a container via health agent.
        
        Args:
            server_ip: Target server IP
            container_name: Container name to restart
            
        Returns:
            True if restart successful
        """
        try:
            log(f"Restarting {container_name} on {server_ip} via agent...")
            
            response = HealthMonitor.agent_request(
                server_ip,
                "POST",
                f"/containers/{container_name}/restart",
                timeout=60
            )
            
            if response.get('status') == 'restarted':
                log(f"✓ Successfully restarted {container_name} on {server_ip}")
                return True
            else:
                log(f"Unexpected response when restarting {container_name}: {response}")
                return False
                
        except Exception as e:
            log(f"Failed to restart {container_name} on {server_ip}: {e}")
            return False

    @staticmethod
    def is_server_healthy(server: Dict[str, Any]) -> bool:
        """
        Check if a server is healthy.
        
        UPDATED: Now includes agent healing before declaring unhealthy.
        
        Args:
            server: Server dict with ip, zone, etc.
            
        Returns:
            True if server passes all health checks
        """
        server_ip = server['ip']
        
        # 1. Ping check
        if not HealthMonitor.ping_server(server_ip):
            log(f"Server {server_ip} failed ping check")
            return False
        
        # 2. Agent check (with auto-healing)
        if not HealthMonitor.check_and_heal_agent(server_ip):
            log(f"Server {server_ip} - agent unavailable after healing attempt")
            return False
        
        # 3. Docker health check
        if not HealthMonitor.check_docker_healthy(server_ip):
            log(f"Server {server_ip} failed Docker health check")
            return False
        
        # 4. Container health check
        missing_containers = HealthMonitor.check_service_containers(server)
        if missing_containers:
            log(f"Server {server_ip} missing containers: {missing_containers}")
            return False
        
        return True
    
    @staticmethod
    def am_i_leader(healthy_servers: List[Dict[str, Any]]) -> bool:
        """Determine if this server is the leader (lowest IP)"""
        if not healthy_servers:
            return False
        
        my_ip = HealthMonitor.get_my_ip()
        leader_ip = sorted([s['ip'] for s in healthy_servers])[0]
        
        return my_ip == leader_ip
    
    # ========================================
    # SERVER REPLACEMENT
    # ========================================

    @staticmethod
    def _build_container_config(service: Dict[str, Any], server_ip: str) -> Dict[str, Any]:
        """
        Build container configuration for agent deployment.
        
        Args:
            service: Service info dict with name, project, env, config
            server_ip: Target server IP
            
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
            host_port = DeploymentPortResolver.get_host_port(project, env, service_name)
            container_port = config['port']
            ports[str(host_port)] = str(container_port)
        
        # Volumes
        volumes = PathResolver.generate_all_volume_mounts(project, env, service_name, server_ip)
        
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
    def replace_server_sequential(failed_server: Dict[str, Any], user: str, credentials: Dict=None) -> bool:
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
            # Get services that were on the failed server (for context)
            failed_services = LiveDeploymentQuery.get_services_on_server(
                server_ip=failed_server['ip'],
                user=user
            )

            if not failed_services:
                log("Server had no services deployed")
                return True
            
            log(f"Server had {len(failed_services)} services: {[s['service'] for s in failed_services]}")
            
            # Get project/env from first service
            project = failed_services[0]['project']
            env = failed_services[0]['env']
            
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
                    log(f"Pushing config/secrets for {project}/{env} via agent...")
                    
                    if not AgentDeployer.push_files_to_server(
                        new_server['ip'],
                        project,
                        env
                    ):
                        log("Failed to push files to new server")
                        DOManager.destroy_droplet(new_server['droplet_id'], credentials=credentials)
                        ServerInventory.release_servers([new_server['ip']], destroy=False, credentials=credentials)
                        continue
                    
                    log("✓ Files pushed successfully")
                    
                    # STEP 4: Deploy services to new server via agent
                    log(f"Deploying {len(services_to_deploy)} services via agent...")
                    
                    # Group services by startup_order
                    services_by_order = {}
                    for service_info in services_to_deploy:
                        startup_order = service_info['config'].get('startup_order', 1)
                        
                        if startup_order not in services_by_order:
                            services_by_order[startup_order] = []
                        
                        services_by_order[startup_order].append(service_info)
                    
                    # Deploy in startup order
                    deployment_success = True
                    
                    for startup_order in sorted(services_by_order.keys()):
                        services = services_by_order[startup_order]
                        
                        log(f"Deploying startup_order {startup_order}: {[s['name'] for s in services]}")
                        
                        for service in services:
                            success = AgentDeployer.deploy_container(
                                new_server['ip'],
                                HealthMonitor._build_container_config(service, new_server['ip'])
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
                    
                    # STEP 5: Install health monitor on new server
                    log(f"Installing health monitor on {new_server['ip']}...")
                    
                    try:
                        # Import here to avoid circular dependency issues
                        try:
                            from .health_monitor_installer import HealthMonitorInstaller
                        except ImportError:
                            from health_monitor_installer import HealthMonitorInstaller
                        
                        monitor_installed = HealthMonitorInstaller.install_on_server(
                            new_server['ip'],
                            user='root'
                        )
                        
                        if not monitor_installed:
                            log(f"Warning: Health monitor installation failed on {new_server['ip']}")
                            # Don't fail the replacement - monitor can be installed later manually
                        else:
                            log(f"✓ Health monitor installed on {new_server['ip']}")
                    
                    except Exception as e:
                        log(f"Warning: Could not install health monitor: {e}")
                        # Don't fail the replacement - monitor can be installed later manually
                    
                    # STEP 6: Final health check
                    time.sleep(10)  # Let everything stabilize
                    
                    if HealthMonitor.check_docker_healthy(new_server['ip']):
                        missing = HealthMonitor.check_service_containers(new_server)
                        
                        if not missing:
                            log(f"✓ Replacement {new_server['ip']} is healthy")
                            
                            # Promote to ACTIVE
                            ServerInventory.update_server_status([new_server['ip']], ServerInventory.STATUS_ACTIVE, credentials=credentials)
                            
                            # Update deployment state
                            for service in services_to_deploy:
                                DeploymentStateManager.add_server_to_service(
                                    user,
                                    service['project'],
                                    service['env'],
                                    service['name'],
                                    new_server['ip']
                                )
                            
                            # Remove failed server from all services
                            DeploymentStateManager.remove_server_from_all_services(user, failed_server['ip'])
                            
                            # STEP 7: Destroy failed server
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