import os
import psutil
from typing import Dict, Any, List
from datetime import datetime, timedelta
from collections import deque

from execute_cmd import CommandExecuter
from logger import Logger
from resource_resolver import ResourceResolver
from deployment_config import DeploymentConfigurer

def log(msg):
    Logger.log(msg)

class MetricsCollector:
    """
    Collect and store metrics for auto-scaling decisions.
    
    Metrics stored in memory with configurable retention.
    Can be extended to persist to database/file for historical analysis.
    """
    
    # Metric retention (keep last N data points)
    MAX_METRIC_HISTORY = 100
    
    # Thresholds for scaling decisions
    DEFAULT_THRESHOLDS = {
        "cpu_scale_up": 80,      # CPU > 80% for scale up
        "cpu_scale_down": 20,    # CPU < 20% for scale down
        "memory_scale_up": 85,   # Memory > 85% for scale up
        "memory_scale_down": 30, # Memory < 30% for scale down
        "requests_per_second_scale_up": 1000,  # RPS > 1000 for scale up
        "requests_per_second_scale_down": 100, # RPS < 100 for scale down
    }
    
    def __init__(self):
        self._metrics_history = {}  # {server_ip: deque([metrics])}
    
    @staticmethod
    def collect_server_metrics(server_ip: str, user: str = "root") -> Dict[str, Any]:
        """
        Collect current metrics from a server.
        
        Returns:
            {
                'timestamp': datetime,
                'cpu_percent': float,
                'memory_percent': float,
                'disk_percent': float,
                'network_rx_bytes': int,
                'network_tx_bytes': int
            }
        """
        try:
            if server_ip == "localhost":
                # Local metrics using psutil
                return {
                    'timestamp': datetime.now(),
                    'cpu_percent': psutil.cpu_percent(interval=1),
                    'memory_percent': psutil.virtual_memory().percent,
                    'disk_percent': psutil.disk_usage('/').percent,
                    'network_rx_bytes': psutil.net_io_counters().bytes_recv,
                    'network_tx_bytes': psutil.net_io_counters().bytes_sent
                }
            else:
                # Remote metrics via SSH
                cpu_cmd = "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1"
                mem_cmd = "free | grep Mem | awk '{print ($3/$2) * 100.0}'"
                disk_cmd = "df -h / | tail -1 | awk '{print $5}' | cut -d'%' -f1"
                
                cpu = float(CommandExecuter.run_cmd(cpu_cmd, server_ip, user).strip())
                memory = float(CommandExecuter.run_cmd(mem_cmd, server_ip, user).strip())
                disk = float(CommandExecuter.run_cmd(disk_cmd, server_ip, user).strip())
                
                return {
                    'timestamp': datetime.now(),
                    'cpu_percent': cpu,
                    'memory_percent': memory,
                    'disk_percent': disk,
                    'network_rx_bytes': 0,  # TODO: Implement network metrics
                    'network_tx_bytes': 0
                }
                
        except Exception as e:
            log(f"Failed to collect metrics from {server_ip}: {e}")
            return None
    
    @staticmethod
    def collect_service_metrics(
        project: str,
        env: str,
        service: str,
        server_ip: str,
        user: str = "root"
    ) -> Dict[str, Any]:
        """
        Collect metrics for a specific service container including RPS from Nginx.
        
        IMPORTANT: CPU is normalized to 0-100% regardless of host CPU count.
        Docker stats shows CPU as % of total host CPU (e.g., 300% on 8-CPU host = 37.5% utilization).
        We normalize this to make thresholds consistent across different host specs.
        
        Returns:
            {
                'timestamp': datetime,
                'cpu_percent': float,  # Normalized 0-100% (not raw docker stats)
                'memory_mb': float,
                'memory_percent': float,
                'rps': float,
                'internal_port': int
            }
        """        
        container_name = ResourceResolver.get_container_name(project, env, service)
        
        try:
            # Get host CPU count for normalization
            host_cpu_count = MetricsCollector._get_host_cpu_count(server_ip, user)
            
            # Get container stats
            stats_cmd = f"docker stats {container_name} --no-stream --format '{{{{.CPUPerc}}}}|{{{{.MemPerc}}}}|{{{{.MemUsage}}}}'"
            result = CommandExecuter.run_cmd(stats_cmd, server_ip, user).strip()
            
            # Parse: "45.67%|23.45%|1.234GiB / 8GiB"
            parts = result.split('|')
            
            raw_cpu_percent = float(parts[0].replace('%', ''))
            memory_percent = float(parts[1].replace('%', ''))
            
            # Normalize CPU: docker stats shows % of total host CPU
            # On 8-CPU host: 300% means using 3 CPUs = 37.5% utilization
            # We normalize to 0-100% scale for consistent thresholds
            cpu_percent = (raw_cpu_percent / (host_cpu_count * 100)) * 100
            
            # Parse memory usage (e.g., "1.234GiB / 8GiB")
            mem_parts = parts[2].split('/')
            mem_used_str = mem_parts[0].strip()
            
            # Convert to MB
            if 'GiB' in mem_used_str:
                memory_mb = float(mem_used_str.replace('GiB', '')) * 1024
            elif 'MiB' in mem_used_str:
                memory_mb = float(mem_used_str.replace('MiB', ''))
            else:
                memory_mb = 0
            
            # Get internal port for this service
            internal_port = ResourceResolver.get_internal_port(project, env, service)
            
            # Collect RPS from Nginx logs - only if service has exposed ports
            rps = 0.0
            
            # Get service config to check for ports
            try:
                config = DeploymentConfigurer(project)
                service_config = config.get_service_config(env, service)
                dockerfile = service_config.get("dockerfile")
                
                # Check if service has container ports
                container_ports = ResourceResolver.get_container_ports(service, dockerfile)
                
                # Only collect RPS if service has ports (is a network service)
                if container_ports:
                    rps = MetricsCollector._collect_nginx_rps_by_port(server_ip, user, internal_port)
                    
            except Exception as e:
                log(f"Could not determine if {service} has ports, skipping RPS collection: {e}")
            
            return {
                'timestamp': datetime.now(),
                'cpu_percent': cpu_percent,  # Normalized 0-100%
                'memory_mb': memory_mb,
                'memory_percent': memory_percent,
                'rps': rps,
                'internal_port': internal_port,
                'container_name': container_name,
                'host_cpu_count': host_cpu_count,  # For debugging
                'raw_cpu_percent': raw_cpu_percent  # For debugging
            }
            
        except Exception as e:
            log(f"Failed to collect service metrics for {service} on {server_ip}: {e}")
            return None
    
    @staticmethod
    def _get_host_cpu_count(server_ip: str, user: str = "root") -> int:
        """
        Get number of CPUs on host server.
        
        Args:
            server_ip: Server IP
            user: SSH user
            
        Returns:
            Number of CPUs (e.g., 8 for 8-CPU host)
        """
        try:
            if server_ip == "localhost":                
                return os.cpu_count() or 1
            else:
                # Remote server via SSH
                cmd = "nproc"
                result = CommandExecuter.run_cmd(cmd, server_ip, user)
                return int(result.strip() or 1)
        except Exception as e:
            log(f"Failed to get CPU count from {server_ip}, defaulting to 1: {e}")
            return 1  # Safe default
    
    @staticmethod
    def _collect_nginx_rps_by_port(server_ip: str, user: str, port: int) -> float:
        """
        Calculate RPS for a service by filtering Nginx logs by internal port.
        
        Args:
            server_ip: Server IP
            user: SSH user
            port: Internal port that Nginx listens on (e.g., 5234)
            
        Returns:
            Requests per second for this port
        """
        try:
            # Count requests to this port in last 60 seconds
            # Assumes nginx log format includes "port:$server_port"
            cmd = f"""
            tail -n 50000 /var/log/nginx/access.log 2>/dev/null | \
            awk -v date="$(date --date='1 minute ago' '+%d/%b/%Y:%H:%M:%S')" \
            '$4 > "["date' | \
            grep 'port:{port}' | \
            wc -l
            """
            
            result = CommandExecuter.run_cmd(cmd, server_ip, user)
            count = int(result.strip() or 0)
            
            # Convert to requests per second
            rps = count / 60.0
            return rps
            
        except Exception as e:
            log(f"Failed to collect RPS for port {port} from {server_ip}: {e}")
            return 0.0
    
    def store_metrics(self, server_ip: str, metrics: Dict[str, Any]):
        """Store metrics in memory with size limit"""
        if server_ip not in self._metrics_history:
            self._metrics_history[server_ip] = deque(maxlen=self.MAX_METRIC_HISTORY)
        
        self._metrics_history[server_ip].append(metrics)
    
    def get_average_metrics(
        self,
        server_ip: str,
        window_minutes: int = 5
    ) -> Dict[str, float]:
        """
        Calculate average metrics over a time window.
        
        IMPORTANT: Requires minimum samples to ensure reliable averages.
        Since metrics are collected every 60s, we need at least 'window_minutes'
        samples before returning averages (cold start protection).
        
        Args:
            server_ip: Server to analyze (can be server_ip or server_ip_service key)
            window_minutes: Time window in minutes
            
        Returns:
            {
                'avg_cpu': float,
                'avg_memory': float,
                'avg_disk': float,
                'avg_rps': float,
                'sample_count': int
            }
            or None if insufficient samples
        """
        if server_ip not in self._metrics_history:
            return None
        
        cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
        recent_metrics = [
            m for m in self._metrics_history[server_ip]
            if m['timestamp'] >= cutoff_time
        ]
        
        if not recent_metrics:
            return None
        
        # Cold start protection: require minimum samples for reliable average
        # If collecting every 60s, need at least 'window_minutes' samples
        min_samples = window_minutes
        if len(recent_metrics) < min_samples:
            log(f"Insufficient samples for {server_ip}: {len(recent_metrics)}/{min_samples} "
                f"(need {min_samples} minutes of data for {window_minutes}-minute average)")
            return None
        
        return {
            'avg_cpu': sum(m['cpu_percent'] for m in recent_metrics) / len(recent_metrics),
            'avg_memory': sum(m['memory_percent'] for m in recent_metrics) / len(recent_metrics),
            'avg_disk': sum(m.get('disk_percent', 0) for m in recent_metrics) / len(recent_metrics),
            'avg_rps': sum(m.get('rps', 0) for m in recent_metrics) / len(recent_metrics),
            'sample_count': len(recent_metrics)
        }