import time
import requests
import socket
import subprocess
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime, timedelta
from server_inventory import ServerInventory
from do_manager import DOManager
from execute_cmd import CommandExecuter
from logger import Logger
from auto_scaling_coordinator import AutoScalingCoordinator
import env_loader

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
                        import time
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

    # ========================================
    # SERVER HEALTH CHECKS
    # ========================================
    
    @staticmethod
    def get_my_ip() -> str:
        """Get this server's IP address"""
        try:
            # Try to get from environment first (set during deployment)
            import os
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
            import platform
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
    def check_docker_healthy(ip: str) -> bool:
        """Check if Docker is running on server via health agent"""
        try:
            response = HealthMonitor.agent_request(ip, "GET", "/health", timeout=5)
            return response.get('docker_running', False)
        except Exception as e:
            log(f"Docker health check failed for {ip}: {e}")
            return False
    
    @staticmethod
    def check_service_containers(server: Dict[str, Any]) -> List[str]:
        """Check which expected containers are missing on a server via health agent."""
        from deployment_state_manager import DeploymentStateManager
        
        ip = server['ip']
        
        # Get expected services on this server
        expected_services = DeploymentStateManager.get_services_on_server(ip)
        
        if not expected_services:
            return []
        
        # Get running containers via agent
        try:
            response = HealthMonitor.agent_request(ip, "GET", "/health", timeout=5)
            running_containers = set(response.get('containers', []))
        except Exception as e:
            log(f"Could not get containers on {ip} via agent: {e}")
            return [s['container_name'] for s in expected_services]
        
        # Find missing containers
        missing = []
        for service in expected_services:
            container_name = service['container_name']
            if container_name not in running_containers:
                missing.append(container_name)
                log(f"Missing container on {ip}: {container_name}")
        
        return missing

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
        """Check if server is healthy (ping + docker + containers)"""
        ip = server['ip']
        
        if not HealthMonitor.ping_server(ip):
            log(f"Server {ip} failed ping check")
            return False
        
        if not HealthMonitor.check_docker_healthy(ip):
            log(f"Server {ip} failed Docker check")
            return False
        
        missing_containers = HealthMonitor.check_service_containers(server)
        if missing_containers:
            log(f"Server {ip} missing {len(missing_containers)} containers")
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
        from path_resolver import PathResolver
        from deployment_naming import DeploymentNaming
        
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
            from deployment_port_resolver import DeploymentPortResolver
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
    def replace_server_sequential(failed_server: Dict[str, Any]) -> bool:
        """
        Replace a failed server using health agent (no SSH needed).
        
        Process:
        1. Get services that were on the failed server
        2. Create replacement with same specs (DO API)
        3. Wait for it to become active
        4. Push config/secrets via agent HTTP
        5. Deploy services via agent HTTP
        6. Health check via agent HTTP
        7. If healthy: destroy old, update state
        8. If unhealthy: destroy replacement, retry (max 3 attempts)
        
        Returns:
            True if replacement successful
        """
        from deployment_state_manager import DeploymentStateManager
        from agent_deployer import AgentDeployer
        
        log(f"Replacing failed server {failed_server['ip']}")
        Logger.start()
        
        # Get services that were on the failed server
        failed_services = DeploymentStateManager.get_services_on_server(failed_server['ip'])
        
        if failed_services:
            log(f"Server had {len(failed_services)} services: {[s['service'] for s in failed_services]}")
        else:
            log("Server had no services deployed")
        
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
                    tags=["deployer", f"replacement_for:{failed_server['ip']}"]
                )
                
                if not new_droplets:
                    log("Failed to create replacement droplet")
                    continue
                
                new_server = new_droplets[0]
                log(f"✓ Created replacement: {new_server['ip']}")
                
                # Add to inventory as blue (not active yet)
                ServerInventory.add_servers([new_server], ServerInventory.STATUS_BLUE)
                
                # STEP 2: Wait for server to be ready (comes from template snapshot)
                import time
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
                    DOManager.destroy_droplet(new_server['droplet_id'])
                    ServerInventory.release_servers([new_server['ip']], destroy=False)
                    continue
                
                # STEP 3: Push config/secrets to new server via agent
                if failed_services:
                    # Get project/env from first service
                    project = failed_services[0]['project']
                    env = failed_services[0]['env']
                    
                    log(f"Pushing config/secrets for {project}/{env} via agent...")
                    
                    if not AgentDeployer.push_files_to_server(
                        new_server['ip'],
                        project,
                        env
                    ):
                        log("Failed to push files to new server")
                        DOManager.destroy_droplet(new_server['droplet_id'])
                        ServerInventory.release_servers([new_server['ip']], destroy=False)
                        continue
                    
                    log("✓ Files pushed successfully")
                    
                    # STEP 4: Deploy services to new server via agent
                    log(f"Deploying {len(failed_services)} services via agent...")
                    
                    # Group services by startup_order
                    from deployment_config import DeploymentConfigurer
                    
                    services_by_order = {}
                    for service_info in failed_services:
                        service_name = service_info['service']
                        project = service_info['project']
                        env = service_info['env']
                        
                        # Get full service config
                        configurer = DeploymentConfigurer(project)
                        all_services = configurer.get_services(env)
                        service_config = all_services.get(service_name, {})
                        
                        startup_order = service_config.get('startup_order', 1)
                        
                        if startup_order not in services_by_order:
                            services_by_order[startup_order] = []
                        
                        services_by_order[startup_order].append({
                            'name': service_name,
                            'project': project,
                            'env': env,
                            'config': service_config
                        })
                    
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
                            container_name = f"{service['project']}_{service['env']}_{service['name']}"
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
                        DOManager.destroy_droplet(new_server['droplet_id'])
                        ServerInventory.release_servers([new_server['ip']], destroy=False)
                        continue
                    
                    log("✓ All services deployed successfully")
                
                # STEP 5: Final health check
                time.sleep(10)  # Let everything stabilize
                
                if HealthMonitor.check_docker_healthy(new_server['ip']):
                    missing = HealthMonitor.check_service_containers(new_server)
                    
                    if not missing:
                        log(f"✓ Replacement {new_server['ip']} is healthy")
                        
                        # STEP 6: Promote to green (active)
                        ServerInventory.update_server_status([new_server['ip']], ServerInventory.STATUS_ACTIVE)
                        
                        # Update deployment state
                        DeploymentStateManager.remove_server_from_all_services(failed_server['ip'])
                        
                        for service in failed_services:
                            DeploymentStateManager.add_server_to_service(
                                service['project'],
                                service['env'],
                                service['service'],
                                new_server['ip']
                            )
                        
                        # STEP 7: Destroy failed server
                        log(f"Destroying failed server {failed_server['ip']}...")
                        DOManager.destroy_droplet(failed_server['droplet_id'])
                        ServerInventory.release_servers([failed_server['ip']], destroy=False)
                        
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
                            f"Services redeployed: {len(failed_services)}\n"
                            f"All autonomous - no manual intervention required."
                        )
                        
                        return True
                    else:
                        log(f"Replacement {new_server['ip']} missing containers: {missing}")
                else:
                    log(f"Replacement {new_server['ip']} failed Docker health check")
                
                # Unhealthy - destroy and retry
                log("Replacement unhealthy, destroying and retrying...")
                DOManager.destroy_droplet(new_server['droplet_id'])
                ServerInventory.release_servers([new_server['ip']], destroy=False)
                
                if attempt == HealthMonitor.MAX_REPLACEMENT_ATTEMPTS:
                    HealthMonitor.record_replacement_attempt(
                        failed_server['ip'],
                        False,
                        f"Failed after {attempt} attempts"
                    )
            
            except Exception as e:
                log(f"Replacement attempt {attempt} failed: {e}")
                import traceback
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
            f"Services affected: {len(failed_services)}\n"
            f"Manual intervention required."
        )
        
        return False

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
        from certificate_manager import CertificateManager
       
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
        import json
        
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
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            import os
            
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