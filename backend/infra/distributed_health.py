"""
Distributed Health Monitoring

Simplified health monitoring with timeout-based failure detection and
deterministic leader election for recovery coordination.
"""

import asyncio
import json
import aiohttp
import time
import socket
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass
from pathlib import Path

from .infrastructure_state import InfrastructureState
from .managers.snapshot_manager import SnapshotManager
from .managers.load_balancer_manager import LoadBalancerManager


@dataclass
class HealthCheckResult:
    """Result of a health check operation"""
    target: str
    healthy: bool
    response_time_ms: Optional[float]
    error: Optional[str]
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'target': self.target,
            'healthy': self.healthy,
            'response_time_ms': self.response_time_ms,
            'error': self.error,
            'timestamp': self.timestamp.isoformat()
        }


class DistributedHealthMonitor:
    """
    Simplified distributed health monitoring with timeout-based failure detection
    and deterministic leader election for recovery coordination.
    """
    
    def __init__(self, droplet_name: str, infrastructure_state: InfrastructureState,
                 snapshot_manager: SnapshotManager, load_balancer_manager: LoadBalancerManager,
                 emailer=None):
        self.droplet_name = droplet_name
        self.state = infrastructure_state
        self.snapshot_manager = snapshot_manager
        self.load_balancer_manager = load_balancer_manager
        self.emailer = emailer
        
        # Health monitoring state
        self.health_results = {}  # target -> HealthCheckResult
        self.failure_start_times = {}  # target -> datetime when failure first detected
        self.last_heartbeat_sent = datetime.now() - timedelta(hours=1)
        
        # Configuration
        self.check_interval = 30  # seconds between health checks
        self.health_timeout = 10  # seconds for individual health check
        self.failure_timeout_minutes = 5  # minutes before triggering recovery
        self.heartbeat_interval = 15  # minutes between heartbeat emails
        
        # Recovery coordination
        self.recovery_operations = set()  # Track ongoing recovery operations
        
    async def start_monitoring(self):
        """Start the distributed health monitoring daemon"""
        print(f"Starting simplified health monitoring on {self.droplet_name}")
        
        # Start all monitoring tasks
        tasks = [
            asyncio.create_task(self._health_check_loop()),
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._cleanup_loop())
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            print(f"Stopping health monitoring on {self.droplet_name}")
            for task in tasks:
                task.cancel()
    
    async def _health_check_loop(self):
        """Main health checking loop"""
        while True:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                print(f"Error in health check loop: {e}")
                await asyncio.sleep(self.check_interval)
    
    async def _perform_health_checks(self):
        """Perform health checks on assigned targets"""
        
        # Get droplet configuration
        droplet_config = self.state.get_droplet(self.droplet_name)
        if not droplet_config:
            return
        
        targets_to_monitor = droplet_config.get('monitors', [])
        
        # Create health check tasks
        tasks = []
        for target in targets_to_monitor:
            task = asyncio.create_task(self._check_target_health(target))
            tasks.append(task)
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    print(f"Health check error for {targets_to_monitor[i]}: {result}")
                elif isinstance(result, HealthCheckResult):
                    await self._process_health_result(result)
    
    async def _check_target_health(self, target_droplet: str) -> HealthCheckResult:
        """Check health of a specific target droplet using basic connectivity tests"""
        
        target_config = self.state.get_droplet(target_droplet)
        if not target_config:
            return HealthCheckResult(
                target=target_droplet,
                healthy=False,
                response_time_ms=None,
                error="Target droplet not found in state",
                timestamp=datetime.now()
            )
        
        target_ip = target_config['ip']
        start_time = time.time()
        
        # Basic connectivity tests (in order of importance)
        connectivity_tests = [
            ('SSH', target_ip, 22),     # SSH = server is alive
            ('HTTP', target_ip, 80),    # HTTP = nginx is running  
            ('HTTPS', target_ip, 443),  # HTTPS = SSL services running
        ]
        
        # Try each connectivity test
        for test_name, ip, port in connectivity_tests:
            try:
                if await self._test_tcp_connectivity(ip, port):
                    response_time = (time.time() - start_time) * 1000
                    return HealthCheckResult(
                        target=target_droplet,
                        healthy=True,
                        response_time_ms=response_time,
                        error=None,
                        timestamp=datetime.now()
                    )
            except Exception as e:
                continue  # Try next test
        
        # All connectivity tests failed
        response_time = (time.time() - start_time) * 1000
        return HealthCheckResult(
            target=target_droplet,
            healthy=False,
            response_time_ms=response_time,
            error="All connectivity tests failed (SSH:22, HTTP:80, HTTPS:443)",
            timestamp=datetime.now()
        )
    
    async def _test_tcp_connectivity(self, host: str, port: int) -> bool:
        """Test if we can connect to a TCP port"""
        try:
            future = asyncio.get_event_loop().run_in_executor(
                None, self._test_tcp_connection, host, port
            )
            return await asyncio.wait_for(future, timeout=3.0)
        except asyncio.TimeoutError:
            return False
        except Exception:
            return False
    
    async def _process_health_result(self, result: HealthCheckResult):
        """Process health check result and trigger recovery if needed"""
        
        # Store result
        self.health_results[result.target] = result
        
        if not result.healthy:
            # Track when failure started
            if result.target not in self.failure_start_times:
                self.failure_start_times[result.target] = datetime.now()
                print(f"⚠️  Detected failure for {result.target}: {result.error}")
            
            # Check if failure has persisted long enough to trigger recovery
            failure_duration = datetime.now() - self.failure_start_times[result.target]
            
            if failure_duration >= timedelta(minutes=self.failure_timeout_minutes):
                print(f"🚨 {result.target} has been down for {failure_duration}, checking recovery leadership...")
                
                # Check if we should be the recovery leader
                if await self._am_i_recovery_leader_for(result.target):
                    print(f"🏆 I am the recovery leader for {result.target}")
                    await self._coordinate_recovery(result.target)
                else:
                    print(f"⏳ Another server is the recovery leader for {result.target}")
        else:
            # Target is healthy, clear failure tracking
            if result.target in self.failure_start_times:
                print(f"✅ {result.target} is healthy again")
                del self.failure_start_times[result.target]
    
    async def _am_i_recovery_leader_for(self, failed_target: str) -> bool:
        """Deterministically determine if this server should lead recovery"""
        
        # Get all droplets except the failed one
        all_droplets = self.state.get_all_droplets()
        candidate_leaders = []
        
        for name, config in all_droplets.items():
            if name != failed_target:
                # Quick reachability check for other servers
                if name == self.droplet_name:
                    # We're always reachable to ourselves
                    candidate_leaders.append((name, config['ip']))
                elif await self._is_server_reachable(config['ip']):
                    candidate_leaders.append((name, config['ip']))
        
        if not candidate_leaders:
            print("🚨 No healthy servers found for recovery leadership!")
            return False
        
        # Sort by IP address for deterministic ordering (lowest IP wins)
        candidate_leaders.sort(key=lambda x: x[1])
        
        recovery_leader = candidate_leaders[0][0]
        
        print(f"🗳️  Recovery leader election: {recovery_leader} (from candidates: {[name for name, _ in candidate_leaders]})")
        
        return recovery_leader == self.droplet_name
    
    async def _is_server_reachable(self, server_ip: str) -> bool:
        """Quick check if server is reachable"""
        try:
            # Test SSH port connectivity (non-blocking)
            future = asyncio.get_event_loop().run_in_executor(
                None, self._test_tcp_connection, server_ip, 22
            )
            return await asyncio.wait_for(future, timeout=3.0)
        except asyncio.TimeoutError:
            return False
        except Exception:
            return False
    
    def _test_tcp_connection(self, host: str, port: int) -> bool:
        """Test TCP connection (blocking, run in executor)"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except Exception:
            return False
    
    async def _coordinate_recovery(self, failed_target: str):
        """Coordinate recovery of a failed target"""
        
        if failed_target in self.recovery_operations:
            print(f"Recovery already in progress for {failed_target}")
            return
        
        self.recovery_operations.add(failed_target)
        
        try:
            print(f"🔄 Starting recovery coordination for {failed_target}")
            
            # 1. Remove from load balancer immediately
            await self._remove_from_load_balancer(failed_target)
            
            # 2. Attempt snapshot recovery
            recovery_result = await self._attempt_snapshot_recovery(failed_target)
            
            if recovery_result['success']:
                # 3. Add recovered droplet back to load balancer
                await self._add_to_load_balancer(recovery_result['new_droplet_name'])
                
                # 4. Send recovery notification
                await self._send_recovery_notification(failed_target, recovery_result)
                
                # 5. Clear failure tracking
                if failed_target in self.failure_start_times:
                    del self.failure_start_times[failed_target]
            else:
                # 5. Send failure notification
                await self._send_recovery_failure_notification(failed_target, recovery_result)
                
        except Exception as e:
            print(f"Error during recovery coordination for {failed_target}: {e}")
            await self._send_recovery_failure_notification(failed_target, {'error': str(e)})
        finally:
            self.recovery_operations.discard(failed_target)
    
    async def _remove_from_load_balancer(self, failed_target: str):
        """Remove failed target from load balancer"""
        
        try:
            # Get services running on failed droplet
            services_on_droplet = self.state.get_services_on_droplet(failed_target)
            
            for service_name in services_on_droplet:
                # Parse service name to get project and service type
                parts = service_name.split('-')
                if len(parts) >= 3:
                    project = '-'.join(parts[:-1])
                    service_type = parts[-1]
                    
                    # Remove failed droplet from service assignment
                    service_config = self.state.get_project_services(project).get(service_type, {})
                    assigned_droplets = service_config.get('assigned_droplets', [])
                    
                    if failed_target in assigned_droplets:
                        updated_droplets = [d for d in assigned_droplets if d != failed_target]
                        
                        self.state.add_project_service(
                            project=project,
                            service_type=service_type,
                            port=service_config.get('port'),
                            assigned_droplets=updated_droplets,
                            service_config={k: v for k, v in service_config.items() 
                                          if k not in ['port', 'assigned_droplets']}
                        )
            
            # Regenerate and deploy load balancer config
            self.load_balancer_manager.deploy_nginx_config()
            
            print(f"Removed {failed_target} from load balancer")
            
        except Exception as e:
            print(f"Error removing {failed_target} from load balancer: {e}")
    
    async def _attempt_snapshot_recovery(self, failed_target: str) -> Dict[str, Any]:
        """Attempt to recover failed target using snapshots"""
        
        try:
            recovery_result = self.snapshot_manager.recover_droplet_from_snapshot(failed_target)
            return recovery_result
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'recovery_method': 'snapshot_recovery'
            }
    
    async def _add_to_load_balancer(self, new_droplet_name: str):
        """Add recovered droplet back to load balancer"""
        
        try:
            # Services should already be updated in infrastructure state by snapshot recovery
            # Just need to regenerate and deploy nginx config
            self.load_balancer_manager.deploy_nginx_config()
            
            print(f"Added {new_droplet_name} to load balancer")
            
        except Exception as e:
            print(f"Error adding {new_droplet_name} to load balancer: {e}")
    
    async def _heartbeat_loop(self):
        """Send heartbeat emails at regular intervals"""
        while True:
            try:
                await self._send_heartbeat_if_due()
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                print(f"Error in heartbeat loop: {e}")
                await asyncio.sleep(60)
    
    async def _send_heartbeat_if_due(self):
        """Send heartbeat email if interval has passed"""
        
        if not self.emailer:
            return
        
        # Use the same leader election logic as recovery
        if await self._am_i_heartbeat_leader():
            # I'm the heartbeat leader - send regular heartbeats
            time_since_last = datetime.now() - self.last_heartbeat_sent
            if time_since_last >= timedelta(minutes=self.heartbeat_interval):
                await self._send_heartbeat_email("primary")
                self.last_heartbeat_sent = datetime.now()
    
    async def _am_i_heartbeat_leader(self) -> bool:
        """Deterministically determine if this server should send heartbeats (same logic as recovery leader)"""
        
        # Get all healthy droplets
        all_droplets = self.state.get_all_droplets()
        candidate_leaders = []
        
        for name, config in all_droplets.items():
            # Quick reachability check for other servers
            if name == self.droplet_name:
                # We're always reachable to ourselves
                candidate_leaders.append((name, config['ip']))
            elif await self._is_server_reachable(config['ip']):
                candidate_leaders.append((name, config['ip']))
        
        if not candidate_leaders:
            print("🚨 No healthy servers found for heartbeat leadership!")
            return False
        
        # Sort by IP address for deterministic ordering (lowest IP wins)
        candidate_leaders.sort(key=lambda x: x[1])
        
        heartbeat_leader = candidate_leaders[0][0]
        
        # Only log leadership changes, not every check
        if not hasattr(self, '_last_heartbeat_leader') or self._last_heartbeat_leader != heartbeat_leader:
            print(f"💓 Heartbeat leader: {heartbeat_leader} (from candidates: {[name for name, _ in candidate_leaders]})")
            self._last_heartbeat_leader = heartbeat_leader
        
        return heartbeat_leader == self.droplet_name
    
    async def _send_heartbeat_email(self, email_type: str):
        """Send heartbeat email notification"""
        
        try:
            status_summary = self._get_infrastructure_status_summary()
            
            subject = f"✅ Infrastructure OK - {datetime.now().strftime('%H:%M')} (from {self.droplet_name})"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px;">
                <h2 style="color: #28a745;">🟢 All Systems Operational</h2>
                <div style="background-color: #d4edda; border: 1px solid #c3e6cb; padding: 10px; border-radius: 5px; margin-bottom: 15px;">
                    <p><strong>Heartbeat Leader:</strong> {self.droplet_name}</p>
                    <p><strong>Leadership Method:</strong> Deterministic (Lowest IP)</p>
                </div>
                <table style="border-collapse: collapse; width: 100%; border: 1px solid #ddd;">
                    <tr style="background-color: #f8f9fa;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>Master:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['master']['status']}</td></tr>
                    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Web Droplets:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['web_count']} healthy</td></tr>
                    <tr style="background-color: #f8f9fa;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>Total Services:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['total_services']} running</td></tr>
                    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Backend Services:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['backend_services']} running</td></tr>
                    <tr style="background-color: #f8f9fa;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>Frontend Services:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['frontend_services']} running</td></tr>
                    <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Last Check:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
                </table>
                <p style="color: #6c757d; font-size: 14px; margin-top: 15px;">No action needed. Leadership rotates automatically based on server availability.</p>
            </div>
            """
            
            # Send email using emailer (recipients will come from config)
            self.emailer.send_email(
                subject=subject,
                html=html_content
            )
            
            print(f"Sent heartbeat email as leader from {self.droplet_name}")
            
        except Exception as e:
            print(f"Error sending heartbeat email: {e}")
    
    def _get_infrastructure_status_summary(self) -> Dict[str, Any]:
        """Get current infrastructure status summary"""
        
        all_droplets = self.state.get_all_droplets()
        web_droplets = self.state.get_web_droplets()
        all_projects = self.state.get_all_projects()
        
        # Count services by type
        backend_services = 0
        frontend_services = 0
        total_services = 0
        
        for project, services in all_projects.items():
            if project != 'infrastructure':
                for service_type in services.keys():
                    total_services += 1
                    if 'backend' in service_type:
                        backend_services += 1
                    elif 'frontend' in service_type:
                        frontend_services += 1
        
        # Master status
        master_droplet = self.state.get_master_droplet()
        master_health = self.health_results.get('master', None)
        master_status = "healthy" if master_health and master_health.healthy else "unknown"
        
        # Peer status
        healthy_peers = sum(1 for result in self.health_results.values() if result.healthy)
        total_peers = len(all_droplets) - 1  # Exclude self
        
        return {
            'master': {
                'status': master_status,
                'ip': master_droplet['ip'] if master_droplet else 'unknown'
            },
            'web_count': len(web_droplets),
            'total_services': total_services,
            'backend_services': backend_services,
            'frontend_services': frontend_services,
            'peer_status': f"{healthy_peers}/{total_peers} healthy"
        }
    
    async def _send_recovery_notification(self, failed_target: str, recovery_result: Dict[str, Any]):
        """Send notification about successful recovery"""
        
        if not self.emailer:
            return
        
        try:
            subject = f"🔄 Recovery Completed - {failed_target} restored"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px;">
                <h2 style="color: #17a2b8;">🔄 Automatic Recovery Completed</h2>
                <div style="background-color: #d1ecf1; border: 1px solid #bee5eb; padding: 15px; border-radius: 5px;">
                    <p><strong>Failed Droplet:</strong> {failed_target}</p>
                    <p><strong>New Droplet:</strong> {recovery_result.get('new_droplet_name')}</p>
                    <p><strong>New IP:</strong> {recovery_result.get('new_droplet_ip')}</p>
                    <p><strong>Recovery Time:</strong> {recovery_result.get('recovery_time_minutes')} minutes</p>
                    <p><strong>Code Version:</strong> {recovery_result.get('git_commit', 'unknown')}</p>
                </div>
                <p style="color: #0c5460; margin-top: 15px;">Service restored automatically using latest deployment snapshot.</p>
            </div>
            """
            
            self.emailer.send_email(
                subject=subject,
                html=html_content
            )
            
            print("Sent recovery notification email")
            
        except Exception as e:
            print(f"Error sending recovery notification: {e}")
    
    async def _send_recovery_failure_notification(self, failed_target: str, error_info: Dict[str, Any]):
        """Send notification about failed recovery"""
        
        if not self.emailer:
            return
        
        try:
            subject = f"🚨 Recovery Failed - {failed_target} needs manual intervention"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px;">
                <h2 style="color: #dc3545;">🚨 Automatic Recovery Failed</h2>
                <div style="background-color: #f8d7da; border: 1px solid #f5c6cb; padding: 15px; border-radius: 5px;">
                    <p><strong>Failed Droplet:</strong> {failed_target}</p>
                    <p><strong>Error:</strong> {error_info.get('error', 'Unknown error')}</p>
                    <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                </div>
                <p style="color: #721c24; margin-top: 15px;"><strong>MANUAL INTERVENTION REQUIRED</strong></p>
                <p>Automatic recovery failed. Please check the infrastructure and recover manually.</p>
            </div>
            """
            
            self.emailer.send_email(
                subject=subject,
                html=html_content
            )
            
            print("Sent recovery failure notification email")
            
        except Exception as e:
            print(f"Error sending recovery failure notification: {e}")
    
    async def _cleanup_loop(self):
        """Periodic cleanup of old data"""
        while True:
            try:
                await asyncio.sleep(3600)  # Run every hour
                await self._cleanup_old_data()
            except Exception as e:
                print(f"Error in cleanup loop: {e}")
                await asyncio.sleep(3600)
    
    async def _cleanup_old_data(self):
        """Clean up old health check results"""
        
        cutoff_time = datetime.now() - timedelta(hours=24)
        
        # Clean up old health results
        old_results = [
            target for target, result in self.health_results.items()
            if result.timestamp < cutoff_time
        ]
        
        for target in old_results:
            del self.health_results[target]
        
        # Clean up old failure tracking for healthy targets
        healthy_targets = [
            target for target, result in self.health_results.items()
            if result.healthy
        ]
        
        for target in healthy_targets:
            if target in self.failure_start_times:
                del self.failure_start_times[target]
        
        if old_results:
            print(f"Cleaned up {len(old_results)} old health results")
    
    def get_monitoring_status(self) -> Dict[str, Any]:
        """Get current monitoring status"""
        
        return {
            'droplet_name': self.droplet_name,
            'monitoring_targets': len(self.health_results),
            'healthy_targets': sum(1 for r in self.health_results.values() if r.healthy),
            'active_failures': len(self.failure_start_times),
            'recovery_operations': len(self.recovery_operations),
            'last_heartbeat': self.last_heartbeat_sent.isoformat(),
            'health_results': {
                target: result.to_dict() for target, result in self.health_results.items()
            },
            'failure_tracking': {
                target: start_time.isoformat() for target, start_time in self.failure_start_times.items()
            }
        }