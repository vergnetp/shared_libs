import time
import psutil
from typing import Dict, Any, List
from datetime import datetime, timedelta
from collections import deque
from execute_cmd import CommandExecuter
from server_inventory import ServerInventory
from logger import Logger

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
        Collect metrics for a specific service container.
        
        Returns:
            {
                'timestamp': datetime,
                'cpu_percent': float,
                'memory_mb': float,
                'memory_percent': float,
                'network_rx_bytes': int,
                'network_tx_bytes': int
            }
        """
        from resource_resolver import ResourceResolver
        
        container_name = ResourceResolver.get_container_name(project, env, service)
        
        try:
            # Get container stats
            stats_cmd = f"docker stats {container_name} --no-stream --format '{{{{.CPUPerc}}}}|{{{{.MemPerc}}}}|{{{{.MemUsage}}}}|{{{{.NetIO}}}}'"
            result = CommandExecuter.run_cmd(stats_cmd, server_ip, user).strip()
            
            # Parse: "45.67%|23.45%|1.234GiB / 8GiB|1.2MB / 3.4MB"
            parts = result.split('|')
            
            cpu_percent = float(parts[0].replace('%', ''))
            memory_percent = float(parts[1].replace('%', ''))
            
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
            
            return {
                'timestamp': datetime.now(),
                'cpu_percent': cpu_percent,
                'memory_mb': memory_mb,
                'memory_percent': memory_percent,
                'container_name': container_name
            }
            
        except Exception as e:
            log(f"Failed to collect service metrics for {service} on {server_ip}: {e}")
            return None
    
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
        
        Args:
            server_ip: Server to analyze
            window_minutes: Time window in minutes
            
        Returns:
            {
                'avg_cpu': float,
                'avg_memory': float,
                'avg_disk': float
            }
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
        
        return {
            'avg_cpu': sum(m['cpu_percent'] for m in recent_metrics) / len(recent_metrics),
            'avg_memory': sum(m['memory_percent'] for m in recent_metrics) / len(recent_metrics),
            'avg_disk': sum(m.get('disk_percent', 0) for m in recent_metrics) / len(recent_metrics),
            'sample_count': len(recent_metrics)
        }