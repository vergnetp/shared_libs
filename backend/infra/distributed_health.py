"""
Distributed Health Monitoring

Implements peer-to-peer health monitoring with consensus-based failure detection,
automated recovery, and heartbeat email notifications.
"""

import asyncio
import json
import aiohttp
import time
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


@dataclass
class FailureConsensus:
    """Tracks failure consensus among peers"""
    failed_target: str
    reporting_peers: Set[str]
    first_reported: datetime
    consensus_reached: bool
    
    def add_peer_report(self, peer: str):
        self.reporting_peers.add(peer)
    
    def check_consensus(self, total_peers: int, required_majority: float = 0.5) -> bool:
        self.consensus_reached = len(self.reporting_peers) >= (total_peers * required_majority)
        return self.consensus_reached


class DistributedHealthMonitor:
    """
    Distributed health monitoring daemon that runs on each droplet
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
        self.failure_consensus = {}  # target -> FailureConsensus
        self.last_heartbeat_sent = datetime.now() - timedelta(hours=1)
        self.peer_health_reports = {}  # peer -> {target -> health_status}
        
        # Configuration
        self.check_interval = 30  # seconds
        self.health_timeout = 10  # seconds
        self.consensus_timeout = 300  # 5 minutes to reach consensus
        self.heartbeat_interval = 15  # minutes
        
        # Recovery coordination
        self.recovery_operations = set()  # Track ongoing recovery operations
        
    async def start_monitoring(self):
        """Start the distributed health monitoring daemon"""
        print(f"Starting distributed health monitoring on {self.droplet_name}")
        
        # Start all monitoring tasks
        tasks = [
            asyncio.create_task(self._health_check_loop()),
            asyncio.create_task(self._consensus_check_loop()),
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
        """Check health of a specific target droplet"""
        
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
        
        # Health check endpoints to try
        health_endpoints = [
            f"http://{target_ip}:8080/health",  # Health check port
            f"http://{target_ip}/health",       # Default nginx health
        ]
        
        # Try each endpoint
        for endpoint in health_endpoints:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.health_timeout)) as session:
                    async with session.get(endpoint) as response:
                        response_time = (time.time() - start_time) * 1000
                        
                        if response.status == 200:
                            return HealthCheckResult(
                                target=target_droplet,
                                healthy=True,
                                response_time_ms=response_time,
                                error=None,
                                timestamp=datetime.now()
                            )
                        else:
                            continue  # Try next endpoint
                            
            except Exception as e:
                continue  # Try next endpoint
        
        # All endpoints failed
        response_time = (time.time() - start_time) * 1000
        return HealthCheckResult(
            target=target_droplet,
            healthy=False,
            response_time_ms=response_time,
            error="All health endpoints failed",
            timestamp=datetime.now()
        )
    
    async def _process_health_result(self, result: HealthCheckResult):
        """Process a health check result and trigger consensus if needed"""
        
        # Store result
        self.health_results[result.target] = result
        
        # If target is unhealthy, start consensus process
        if not result.healthy:
            await self._report_failure_to_peers(result.target, result.error)
            
            # Check if we need to start consensus tracking
            if result.target not in self.failure_consensus:
                self.failure_consensus[result.target] = FailureConsensus(
                    failed_target=result.target,
                    reporting_peers={self.droplet_name},
                    first_reported=datetime.now(),
                    consensus_reached=False
                )
        else:
            # Target is healthy, remove from failure tracking
            if result.target in self.failure_consensus:
                del self.failure_consensus[result.target]
    
    async def _report_failure_to_peers(self, failed_target: str, error: str):
        """Report failure to peer droplets for consensus"""
        
        # Get all droplets to notify
        all_droplets = self.state.get_all_droplets()
        peer_droplets = [name for name in all_droplets.keys() if name != self.droplet_name]
        
        failure_report = {
            'reporter': self.droplet_name,
            'failed_target': failed_target,
            'error': error,
            'timestamp': datetime.now().isoformat()
        }
        
        # Send failure report to each peer
        for peer in peer_droplets:
            try:
                await self._send_failure_report_to_peer(peer, failure_report)
            except Exception as e:
                print(f"Failed to send failure report to {peer}: {e}")
    
    async def _send_failure_report_to_peer(self, peer_droplet: str, report: Dict[str, Any]):
        """Send failure report to a specific peer"""
        
        peer_config = self.state.get_droplet(peer_droplet)
        if not peer_config:
            return
        
        peer_ip = peer_config['ip']
        endpoint = f"http://{peer_ip}:8080/health/failure-report"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, json=report, timeout=aiohttp.ClientTimeout(total=5)) as response:
                    if response.status == 200:
                        print(f"Failure report sent to {peer_droplet}")
        except Exception as e:
            print(f"Failed to send failure report to {peer_droplet}: {e}")
    
    async def _consensus_check_loop(self):
        """Check for failure consensus and trigger recovery"""
        while True:
            try:
                await self._check_failure_consensus()
                await asyncio.sleep(30)  # Check consensus every 30 seconds
            except Exception as e:
                print(f"Error in consensus check loop: {e}")
                await asyncio.sleep(30)
    
    async def _check_failure_consensus(self):
        """Check if consensus has been reached for any failures"""
        
        total_droplets = len(self.state.get_all_droplets())
        
        for target, consensus in list(self.failure_consensus.items()):
            if consensus.check_consensus(total_droplets):
                print(f"Consensus reached for failed target: {target}")
                await self._handle_consensus_failure(target, consensus)
                
                # Remove from tracking after handling
                del self.failure_consensus[target]
            elif datetime.now() - consensus.first_reported > timedelta(seconds=self.consensus_timeout):
                # Consensus timeout reached, remove from tracking
                print(f"Consensus timeout for {target}, removing from tracking")
                del self.failure_consensus[target]
    
    async def _handle_consensus_failure(self, failed_target: str, consensus: FailureConsensus):
        """Handle a target that has reached failure consensus"""
        
        # Determine recovery leader (droplet with lowest IP to avoid conflicts)
        all_healthy_droplets = [
            name for name, droplet in self.state.get_all_droplets().items()
            if name != failed_target and name not in [c.failed_target for c in self.failure_consensus.values()]
        ]
        
        if not all_healthy_droplets:
            print("No healthy droplets available for recovery coordination")
            return
        
        # Sort by IP to get deterministic leader
        healthy_ips = [(name, self.state.get_droplet(name)['ip']) for name in all_healthy_droplets]
        healthy_ips.sort(key=lambda x: x[1])
        recovery_leader = healthy_ips[0][0]
        
        if recovery_leader == self.droplet_name:
            print(f"Acting as recovery leader for failed target: {failed_target}")
            await self._coordinate_recovery(failed_target)
        else:
            print(f"Recovery will be coordinated by {recovery_leader} for {failed_target}")
    
    async def _coordinate_recovery(self, failed_target: str):
        """Coordinate recovery of a failed target"""
        
        if failed_target in self.recovery_operations:
            print(f"Recovery already in progress for {failed_target}")
            return
        
        self.recovery_operations.add(failed_target)
        
        try:
            print(f"Starting recovery coordination for {failed_target}")
            
            # 1. Remove from load balancer immediately
            await self._remove_from_load_balancer(failed_target)
            
            # 2. Attempt snapshot recovery
            recovery_result = await self._attempt_snapshot_recovery(failed_target)
            
            if recovery_result['success']:
                # 3. Add recovered droplet back to load balancer
                await self._add_to_load_balancer(recovery_result['new_droplet_name'])
                
                # 4. Send recovery notification
                await self._send_recovery_notification(failed_target, recovery_result)
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
            # Update infrastructure state to mark droplet as failed
            # This will automatically regenerate nginx config without the failed droplet
            
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
        
        heartbeat_config = self.state.get_heartbeat_config()
        primary_sender = heartbeat_config.get('primary_sender', 'master')
        backup_senders = heartbeat_config.get('backup_senders', [])
        interval_minutes = heartbeat_config.get('interval_minutes', 15)
        
        should_send = False
        email_type = "primary"
        
        if self.droplet_name == primary_sender:
            # Primary sender sends regular heartbeats
            time_since_last = datetime.now() - self.last_heartbeat_sent
            if time_since_last >= timedelta(minutes=interval_minutes):
                should_send = True
                email_type = "primary"
        elif self.droplet_name in backup_senders:
            # Backup senders only send if master is down
            master_healthy = await self._check_master_health()
            if not master_healthy:
                time_since_last = datetime.now() - self.last_heartbeat_sent
                if time_since_last >= timedelta(minutes=10):  # More frequent for backup
                    should_send = True
                    email_type = "backup"
        
        if should_send:
            await self._send_heartbeat_email(email_type)
            self.last_heartbeat_sent = datetime.now()
    
    async def _check_master_health(self) -> bool:
        """Check if master droplet is healthy"""
        
        master_droplet = self.state.get_master_droplet()
        if not master_droplet:
            return False
        
        master_name = None
        for name, droplet in self.state.get_all_droplets().items():
            if droplet == master_droplet:
                master_name = name
                break
        
        if not master_name or master_name == self.droplet_name:
            return True  # We are the master or master not found
        
        # Check if we have recent health data for master
        master_health = self.health_results.get(master_name)
        if master_health and master_health.healthy:
            # Check if health data is recent (within last 5 minutes)
            if datetime.now() - master_health.timestamp < timedelta(minutes=5):
                return True
        
        return False
    
    async def _send_heartbeat_email(self, email_type: str):
        """Send heartbeat email notification"""
        
        try:
            status_summary = self._get_infrastructure_status_summary()
            
            if email_type == "primary":
                subject = f"✅ Infrastructure OK - {datetime.now().strftime('%H:%M')}"
                
                html_content = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px;">
                    <h2 style="color: #28a745;">🟢 All Systems Operational</h2>
                    <table style="border-collapse: collapse; width: 100%; border: 1px solid #ddd;">
                        <tr style="background-color: #f8f9fa;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>Master:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['master']['status']}</td></tr>
                        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Web Droplets:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['web_count']} healthy</td></tr>
                        <tr style="background-color: #f8f9fa;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>Total Services:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['total_services']} running</td></tr>
                        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Backend Services:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['backend_services']} running</td></tr>
                        <tr style="background-color: #f8f9fa;"><td style="padding: 8px; border: 1px solid #ddd;"><strong>Frontend Services:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{status_summary['frontend_services']} running</td></tr>
                        <tr><td style="padding: 8px; border: 1px solid #ddd;"><strong>Last Check:</strong></td><td style="padding: 8px; border: 1px solid #ddd;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</td></tr>
                    </table>
                    <p style="color: #6c757d; font-size: 14px; margin-top: 15px;">No action needed.</p>
                </div>
                """
                
            else:  # backup
                subject = f"⚠️ Backup Heartbeat - Master may be down - {datetime.now().strftime('%H:%M')}"
                
                html_content = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px;">
                    <h2 style="color: #ffc107;">⚠️ Backup Heartbeat from {self.droplet_name}</h2>
                    <div style="background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px;">
                        <p><strong>Master status:</strong> {status_summary['master']['status']}</p>
                        <p><strong>This droplet:</strong> Healthy</p>
                        <p><strong>Other peers:</strong> {status_summary['peer_status']}</p>
                    </div>
                    <p style="color: #856404; margin-top: 15px;">Master droplet may need attention.</p>
                </div>
                """
            
            # Send email using your emailer
            self.emailer.send_email(
                subject=subject,
                recipients=["admin@yourdomain.com"],  # Configure this
                html=html_content
            )
            
            print(f"Sent {email_type} heartbeat email")
            
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
                recipients=["admin@yourdomain.com"],
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
                recipients=["admin@yourdomain.com"],
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
        """Clean up old health check results and consensus data"""
        
        cutoff_time = datetime.now() - timedelta(hours=24)
        
        # Clean up old health results
        old_results = [
            target for target, result in self.health_results.items()
            if result.timestamp < cutoff_time
        ]
        
        for target in old_results:
            del self.health_results[target]
        
        # Clean up old consensus data
        old_consensus = [
            target for target, consensus in self.failure_consensus.items()
            if consensus.first_reported < cutoff_time
        ]
        
        for target in old_consensus:
            del self.failure_consensus[target]
        
        if old_results or old_consensus:
            print(f"Cleaned up {len(old_results)} old health results and {len(old_consensus)} old consensus data")
    
    def get_monitoring_status(self) -> Dict[str, Any]:
        """Get current monitoring status"""
        
        return {
            'droplet_name': self.droplet_name,
            'monitoring_targets': len(self.health_results),
            'healthy_targets': sum(1 for r in self.health_results.values() if r.healthy),
            'active_failures': len(self.failure_consensus),
            'recovery_operations': len(self.recovery_operations),
            'last_heartbeat': self.last_heartbeat_sent.isoformat(),
            'health_results': {
                target: result.to_dict() for target, result in self.health_results.items()
            }
        }
