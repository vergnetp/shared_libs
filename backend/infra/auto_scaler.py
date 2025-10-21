import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from metrics_collector import MetricsCollector
from server_inventory import ServerInventory
from project_deployer import ProjectDeployer
from logger import Logger

def log(msg):
    Logger.log(msg)

class AutoScaler:
    """
    Automated scaling engine for services.
    
    Supports:
    - Horizontal scaling (add/remove servers based on traffic)
    - Vertical scaling (upgrade/downgrade server specs based on resource usage)
    """
    
    # Cooldown periods to prevent flapping
    SCALE_UP_COOLDOWN = 300      # 5 minutes
    SCALE_DOWN_COOLDOWN = 600    # 10 minutes
    
    # Minimum/maximum constraints
    MIN_SERVERS = 1
    MAX_SERVERS = 20
    
    def __init__(self, project: str):
        self.project = project
        self.metrics_collector = MetricsCollector()
        self.last_scale_action = {}  # {service: {action: timestamp}}
    
    def should_scale_horizontally(
        self,
        service: str,
        env: str,
        current_count: int,
        avg_metrics: Dict[str, float],
        thresholds: Dict[str, float]
    ) -> Optional[str]:
        """
        Determine if horizontal scaling is needed.
        
        Returns:
            "scale_up", "scale_down", or None
        """
        # Check cooldown
        if not self._can_scale(service, "horizontal"):
            log(f"Horizontal scaling for {service} in cooldown period")
            return None
        
        # Scale up conditions
        if (avg_metrics['avg_cpu'] > thresholds['cpu_scale_up'] or
            avg_metrics['avg_memory'] > thresholds['memory_scale_up']):
            
            if current_count < self.MAX_SERVERS:
                log(f"Horizontal scale UP needed for {service}: "
                    f"CPU={avg_metrics['avg_cpu']:.1f}% Memory={avg_metrics['avg_memory']:.1f}%")
                return "scale_up"
        
        # Scale down conditions
        elif (avg_metrics['avg_cpu'] < thresholds['cpu_scale_down'] and
              avg_metrics['avg_memory'] < thresholds['memory_scale_down']):
            
            if current_count > self.MIN_SERVERS:
                log(f"Horizontal scale DOWN possible for {service}: "
                    f"CPU={avg_metrics['avg_cpu']:.1f}% Memory={avg_metrics['avg_memory']:.1f}%")
                return "scale_down"
        
        return None
    
    def should_scale_vertically(
        self,
        service: str,
        env: str,
        current_cpu: int,
        current_memory: int,
        avg_metrics: Dict[str, float],
        thresholds: Dict[str, float]
    ) -> Optional[Dict[str, int]]:
        """
        Determine if vertical scaling is needed.
        
        Returns:
            {"cpu": new_cpu, "memory": new_memory} or None
        """
        # Check cooldown
        if not self._can_scale(service, "vertical"):
            log(f"Vertical scaling for {service} in cooldown period")
            return None
        
        # Vertical scale up
        if avg_metrics['avg_memory'] > thresholds['memory_scale_up']:
            new_memory = self._next_memory_size(current_memory, "up")
            if new_memory != current_memory:
                log(f"Vertical scale UP needed for {service}: Memory {current_memory}MB -> {new_memory}MB")
                return {"cpu": current_cpu, "memory": new_memory}
        
        # Vertical scale down
        elif avg_metrics['avg_memory'] < thresholds['memory_scale_down']:
            new_memory = self._next_memory_size(current_memory, "down")
            if new_memory != current_memory:
                log(f"Vertical scale DOWN possible for {service}: Memory {current_memory}MB -> {new_memory}MB")
                return {"cpu": current_cpu, "memory": new_memory}
        
        return None
    
    def execute_horizontal_scale(
        self,
        service: str,
        env: str,
        action: str,
        current_count: int
    ) -> bool:
        """
        Execute horizontal scaling action.
        
        Args:
            service: Service name
            env: Environment
            action: "scale_up" or "scale_down"
            current_count: Current server count
            
        Returns:
            True if successful
        """
        try:
            project = ProjectDeployer(self.project)
            
            if action == "scale_up":
                new_count = min(current_count + 1, self.MAX_SERVERS)
            else:  # scale_down
                new_count = max(current_count - 1, self.MIN_SERVERS)
            
            log(f"Horizontal scaling {service}: {current_count} -> {new_count} servers")
            
            # Update service configuration
            project.update_service(service, servers_count=new_count)
            
            # Deploy changes
            success = project.deploy(env=env, service=service, build=False)
            
            if success:
                self._record_scale_action(service, "horizontal")
                log(f"✓ Horizontal scaling complete for {service}")
            
            return success
            
        except Exception as e:
            log(f"✗ Horizontal scaling failed for {service}: {e}")
            return False
    
    def execute_vertical_scale(
        self,
        service: str,
        env: str,
        new_specs: Dict[str, int]
    ) -> bool:
        """
        Execute vertical scaling action.
        
        Args:
            service: Service name
            env: Environment
            new_specs: {"cpu": int, "memory": int}
            
        Returns:
            True if successful
        """
        try:
            project = ProjectDeployer(self.project)
            
            log(f"Vertical scaling {service}: CPU={new_specs['cpu']} Memory={new_specs['memory']}MB")
            
            # Update service configuration
            project.update_service(
                service,
                server_cpu=new_specs['cpu'],
                server_memory=new_specs['memory']
            )
            
            # Deploy changes (creates new servers with new specs)
            success = project.deploy(env=env, service=service, build=False)
            
            if success:
                self._record_scale_action(service, "vertical")
                log(f"✓ Vertical scaling complete for {service}")
            
            return success
            
        except Exception as e:
            log(f"✗ Vertical scaling failed for {service}: {e}")
            return False
    
    def _can_scale(self, service: str, scale_type: str) -> bool:
        """Check if scaling action is allowed (cooldown check)"""
        if service not in self.last_scale_action:
            return True
        
        if scale_type not in self.last_scale_action[service]:
            return True
        
        last_action = self.last_scale_action[service][scale_type]
        elapsed = (datetime.now() - last_action).total_seconds()
        
        cooldown = self.SCALE_UP_COOLDOWN if scale_type == "scale_up" else self.SCALE_DOWN_COOLDOWN
        
        return elapsed > cooldown
    
    def _record_scale_action(self, service: str, scale_type: str):
        """Record scaling action timestamp"""
        if service not in self.last_scale_action:
            self.last_scale_action[service] = {}
        
        self.last_scale_action[service][scale_type] = datetime.now()
    
    @staticmethod
    def _next_memory_size(current_mb: int, direction: str) -> int:
        """Calculate next memory size based on standard DO sizes"""
        memory_sizes = [1024, 2048, 4096, 8192, 16384, 32768, 65536]
        
        try:
            current_idx = memory_sizes.index(current_mb)
            
            if direction == "up":
                return memory_sizes[min(current_idx + 1, len(memory_sizes) - 1)]
            else:  # down
                return memory_sizes[max(current_idx - 1, 0)]
        except ValueError:
            # Current size not in standard sizes, return closest
            return min(memory_sizes, key=lambda x: abs(x - current_mb))