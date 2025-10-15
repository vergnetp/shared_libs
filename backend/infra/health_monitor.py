import time
import socket
import subprocess
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime, timedelta
from server_inventory import ServerInventory
from do_manager import DOManager
from deployment_naming import DeploymentNaming
from execute_docker import DockerExecuter
from execute_cmd import CommandExecuter
from logger import Logger
import env_loader

def log(msg):
    Logger.log(msg)


class HealthMonitor:
    """
    Distributed health monitoring with leader-based coordination.
    
    Every server monitors all others. Lowest healthy IP becomes leader
    and coordinates replacements. Simple, no distributed locks needed.
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
    def ping_server(ip: str, timeout: int = PING_TIMEOUT) -> bool:
        """Check if server is reachable via ping"""
        try:
            # Use ping command (cross-platform)
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
        """Check if Docker is running on server"""
        try:
            CommandExecuter.run_cmd("docker ps", ip, "root")
            return True
        except Exception:
            return False
    
    @staticmethod
    def check_service_containers(server: Dict[str, Any]) -> List[str]:
        """
        Check which expected containers are missing on a server.
        
        Returns:
            List of missing container names
        """
        from deployment_state_manager import DeploymentStateManager
        
        ip = server['ip']
        
        # Get expected services on this server
        expected_services = DeploymentStateManager.get_services_on_server(ip)
        
        if not expected_services:
            # Server has no services deployed (might be fresh/reserve)
            return []
        
        # Get running containers
        try:
            result = CommandExecuter.run_cmd(
                "docker ps --format '{{.Names}}'",
                ip, "root"
            )
            running = result.stdout.strip().split('\n') if hasattr(result, 'stdout') else str(result).strip().split('\n')
            running_containers = set(c.strip() for c in running if c.strip())
        except Exception as e:
            log(f"Could not get containers on {ip}: {e}")
            return [s['container_name'] for s in expected_services]
        
        # Find missing containers
        missing = []
        for service in expected_services:
            container_name = service['container_name']
            if container_name not in running_containers:
                missing.append(container_name)
                log(f"Missing container on {ip}: {container_name} ({service['project']}/{service['env']}/{service['service']})")
        
        return missing
    
    @staticmethod
    def is_server_healthy(server: Dict[str, Any]) -> bool:
        """Check if server is healthy (ping + docker + containers)"""
        ip = server['ip']
        
        # First check basic connectivity
        if not HealthMonitor.ping_server(ip):
            log(f"Server {ip} failed ping check")
            return False
        
        # Then check Docker is running
        if not HealthMonitor.check_docker_healthy(ip):
            log(f"Server {ip} failed Docker check")
            return False
        
        # Check expected containers are running
        missing_containers = HealthMonitor.check_service_containers(server)
        if missing_containers:
            log(f"Server {ip} missing {len(missing_containers)} containers: {missing_containers}")
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
        
        # Keep only last 100 entries
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
            
            # Gmail SMTP configuration
            smtp_host = "smtp.gmail.com"
            smtp_port = 587
            email = os.getenv("ADMIN_EMAIL", "robinworld.contact@gmail.com")
            password = os.getenv("GMAIL_APP_PASSWORD")

            if not password:
                log("No GMAIL_APP_PASSWORD configured, skipping alert")
                return
            
            # Create message
            msg = MIMEMultipart()
            msg['From'] = email
            msg['To'] = email
            msg['Subject'] = f"[Health Monitor] {subject}"
            
            msg.attach(MIMEText(message, 'plain'))
            
            # Send via Gmail
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(email, password)
                server.send_message(msg)
            
            log(f"Alert sent to {email}")
            
        except Exception as e:
            log(f"Failed to send alert email: {e}")
    
    @staticmethod
    def replace_server_sequential(failed_server: Dict[str, Any]) -> bool:
        """
        Replace a failed server sequentially with retries.
        
        Process:
        1. Get services that were on the failed server
        2. Create replacement with same specs
        3. Wait for it to become active
        4. Install Docker + Health Monitor
        5. Redeploy services to new server
        6. Health check
        7. If healthy: destroy old, mark replacement as green, update deployment state
        8. If unhealthy: destroy replacement, try again (max 3 attempts)
        
        Returns:
            True if replacement successful
        """
        from deployment_state_manager import DeploymentStateManager
        from deployer import Deployer
        
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
                # Create replacement with same specs (parallel=False for single server)
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
                log(f"Created replacement: {new_server['ip']}")
                
                # Add to inventory as blue
                ServerInventory.add_servers([new_server], ServerInventory.STATUS_BLUE)
                
                # Redeploy services to new server
                if failed_services:
                    log(f"Redeploying {len(failed_services)} services to new server...")
                    
                    # Group services by project
                    services_by_project = {}
                    for service in failed_services:
                        project = service['project']
                        if project not in services_by_project:
                            services_by_project[project] = []
                        services_by_project[project].append(service)
                    
                    # Redeploy each service
                    for project, services in services_by_project.items():
                        deployer = Deployer(project, auto_sync=False)
                        
                        for service in services:
                            env = service['env']
                            service_name = service['service']
                            
                            # Get service config
                            service_config = deployer.deployment_configurer.get_services(env).get(service_name)
                            
                            if not service_config:
                                log(f"Warning: Could not find config for {service_name}")
                                continue
                            
                            # Create network if needed
                            deployer.create_containers_network(env, new_server['ip'])
                            
                            # Start service on new server
                            success = deployer.start_long_running_service(
                                project_name=project,
                                env=env,
                                service_name=service_name,
                                service_config=service_config,
                                server_ip=new_server['ip'],
                                user="root"
                            )
                            
                            if success:
                                log(f"Successfully redeployed {service_name} to {new_server['ip']}")
                            else:
                                log(f"Failed to redeploy {service_name} to {new_server['ip']}")
                                raise Exception(f"Service {service_name} redeployment failed")
                
                # Wait for everything to stabilize
                time.sleep(30)
                
                # Health check (Docker + containers)
                if HealthMonitor.check_docker_healthy(new_server['ip']):
                    # Check containers are running
                    missing = HealthMonitor.check_service_containers(new_server)
                    
                    if not missing:
                        log(f"Replacement {new_server['ip']} is healthy with all containers running")
                        
                        # Promote to green
                        ServerInventory.update_server_status([new_server['ip']], ServerInventory.STATUS_GREEN)
                        
                        # Update deployment state
                        log("Updating deployment state...")
                        DeploymentStateManager.remove_server_from_all_services(failed_server['ip'])
                        
                        for service in failed_services:
                            DeploymentStateManager.add_server_to_service(
                                service['project'],
                                service['env'],
                                service['service'],
                                new_server['ip']
                            )
                            log(f"Updated {service['project']}/{service['env']}/{service['service']} to use {new_server['ip']}")
                        
                        # Destroy failed server
                        DOManager.destroy_droplet(failed_server['droplet_id'])
                        ServerInventory.release_servers([failed_server['ip']], destroy=False)
                        
                        HealthMonitor.record_replacement_attempt(
                            failed_server['ip'], 
                            True, 
                            f"Replaced with {new_server['ip']}"
                        )
                        
                        Logger.end()
                        log(f"Successfully replaced {failed_server['ip']} with {new_server['ip']}")
                        
                        # Send success notification
                        HealthMonitor.send_alert(
                            "Server Replacement Successful",
                            f"Failed server {failed_server['ip']} has been replaced with {new_server['ip']}\n"
                            f"Services redeployed: {len(failed_services)}\n"
                            f"All containers are running and healthy."
                        )
                        
                        return True
                    else:
                        log(f"Replacement {new_server['ip']} missing containers: {missing}")
                else:
                    log(f"Replacement {new_server['ip']} failed Docker health check")
                
                # Unhealthy - destroy and retry
                log(f"Replacement {new_server['ip']} failed health check")
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
                if attempt == HealthMonitor.MAX_REPLACEMENT_ATTEMPTS:
                    HealthMonitor.record_replacement_attempt(
                        failed_server['ip'],
                        False,
                        f"Exception: {str(e)}"
                    )
        
        Logger.end()
        log(f"Failed to replace {failed_server['ip']} after {HealthMonitor.MAX_REPLACEMENT_ATTEMPTS} attempts")
        
        # Send failure alert
        HealthMonitor.send_alert(
            "Server Replacement FAILED",
            f"CRITICAL: Failed to replace server {failed_server['ip']} after {HealthMonitor.MAX_REPLACEMENT_ATTEMPTS} attempts.\n"
            f"Services affected: {len(failed_services)}\n"
            f"Manual intervention required."
        )
        
        return False
    
    @staticmethod
    def monitor_and_heal():
        """
        Main monitoring loop - runs on every server.
        Only leader takes action.
        """
        log("Running health check...")
        
        # Sync inventory with DigitalOcean to get fresh state
        try:
            ServerInventory.sync_with_digitalocean()
        except Exception as e:
            log(f"Warning: Could not sync with DigitalOcean: {e}")
        
        # Get all green servers
        all_servers = ServerInventory.get_servers(deployment_status=ServerInventory.STATUS_GREEN)
        
        if not all_servers:
            log("No green servers in inventory")
            return
        
        # Check health of each server
        healthy_servers = []
        failed_servers = []
        
        for server in all_servers:
            if HealthMonitor.is_server_healthy(server):
                healthy_servers.append(server)
            else:
                failed_servers.append(server)
        
        log(f"Health check: {len(healthy_servers)} healthy, {len(failed_servers)} failed")
        
        if not healthy_servers:
            log("CRITICAL: No healthy servers! Cannot perform replacements.")
            HealthMonitor.send_alert(
                "CRITICAL: All Servers Down",
                "All green servers have failed health checks. System is DOWN.\n"
                "Immediate manual intervention required."
            )
            return
        
        # Check if I'm the leader
        if not HealthMonitor.am_i_leader(healthy_servers):
            my_ip = HealthMonitor.get_my_ip()
            leader_ip = sorted([s['ip'] for s in healthy_servers])[0]
            log(f"I am follower ({my_ip}). Leader is {leader_ip}")
            return
        
        log(f"I am leader ({HealthMonitor.get_my_ip()})")
        
        # Leader handles replacements
        if failed_servers:
            # Check minimum healthy servers constraint
            if len(healthy_servers) <= HealthMonitor.MIN_HEALTHY_SERVERS:
                log(f"Cannot replace - would drop below {HealthMonitor.MIN_HEALTHY_SERVERS} healthy servers")
                HealthMonitor.send_alert(
                    "Warning: Cannot Replace Failed Servers",
                    f"Failed servers detected: {[s['ip'] for s in failed_servers]}\n"
                    f"But only {len(healthy_servers)} healthy servers remain.\n"
                    f"Minimum threshold: {HealthMonitor.MIN_HEALTHY_SERVERS}\n"
                    f"Not replacing to avoid total system failure."
                )
                return
            
            # Replace failed servers sequentially
            for failed_server in failed_servers:
                log(f"Detected failed server: {failed_server['ip']}")
                
                success = HealthMonitor.replace_server_sequential(failed_server)
                
                if not success:
                    log(f"Failed to replace {failed_server['ip']} - stopping replacements")
                    break
    
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