import time
from typing import Dict, Any, Optional
from datetime import datetime

try:
    from .metrics_collector import MetricsCollector
except ImportError:
    from metrics_collector import MetricsCollector
try:
    from .project_deployer import ProjectDeployer
except ImportError:
    from project_deployer import ProjectDeployer
try:
    from .logger import Logger
except ImportError:
    from logger import Logger


def log(msg):
    Logger.log(msg)


class AutoScaler:
    """
    Automated scaling engine for services.
    
    Strategy:
    - Horizontal scaling: Add/remove servers based on RPS (request traffic)
    - Vertical scaling: Upgrade/downgrade server specs based on CPU/Memory usage
    
    Cooldown Protection:
    - Scale-up: 5 minutes (react quickly to load spikes)
    - Scale-down: 10 minutes (be conservative to avoid flapping)
    """
    
    # Cooldown periods to prevent flapping
    SCALE_UP_COOLDOWN = 300      # 5 minutes
    SCALE_DOWN_COOLDOWN = 600    # 10 minutes
    
    # Minimum/maximum constraints
    MIN_SERVERS = 1
    MAX_SERVERS = 20
    
    # DigitalOcean size tiers (CPU, Memory in MB)
    SIZE_TIERS = [
        (1, 1024), (1, 2048), (2, 2048), (2, 4096),
        (4, 8192), (8, 16384), (16, 32768), (24, 48192), (32, 65536)
    ]
    
    def __init__(self, project: str):
        self.project = project
        self.metrics_collector = MetricsCollector()
        # Format: {service: {action_key: timestamp}}
        # action_key examples: "horizontal_up", "horizontal_down", "vertical_up", "vertical_down"
        self.last_scale_action = {}
    
    # ========================================
    # HORIZONTAL SCALING (RPS-based)
    # ========================================
    
    def should_scale_horizontally(
        self,
        service: str,
        env: str,
        current_count: int,
        avg_metrics: Dict[str, float],
        thresholds: Dict[str, float]
    ) -> Optional[str]:
        """
        Determine if horizontal scaling is needed based on request traffic (RPS).
        
        Args:
            service: Service name
            env: Environment
            current_count: Current number of servers
            avg_metrics: Averaged metrics including 'avg_rps'
            thresholds: Scaling thresholds including 'rps_scale_up', 'rps_scale_down'
            
        Returns:
            "scale_up", "scale_down", or None
        """
        # Check cooldown
        if not self._can_scale(service, "horizontal"):
            log(f"[{service}] Horizontal scaling in cooldown period")
            return None
        
        # Get RPS (requests per second)
        avg_rps = avg_metrics.get('avg_rps')
        
        # Validate RPS data
        if avg_rps is None:
            log(f"[{service}] No RPS data available, skipping horizontal scaling")
            return None
        
        # Get thresholds
        rps_up = thresholds.get('rps_scale_up', 500)
        rps_down = thresholds.get('rps_scale_down', 50)
        
        # Scale up: Too many requests per server
        if avg_rps > rps_up:
            if current_count >= self.MAX_SERVERS:
                log(f"[{service}] At MAX_SERVERS ({self.MAX_SERVERS}), cannot scale up")
                return None
            
            log(f"[{service}] Horizontal scale UP needed: RPS={avg_rps:.1f} > {rps_up}")
            return "scale_up"
        
        # Scale down: Too few requests per server
        elif avg_rps < rps_down:
            if current_count <= self.MIN_SERVERS:
                log(f"[{service}] At MIN_SERVERS ({self.MIN_SERVERS}), cannot scale down")
                return None
            
            # Additional safety: Don't scale down on suspiciously low RPS (might be metrics issue)
            if avg_rps < 0.1:
                log(f"[{service}] RPS suspiciously low ({avg_rps:.2f}), might be metrics issue, skipping scale down")
                return None
            
            log(f"[{service}] Horizontal scale DOWN possible: RPS={avg_rps:.1f} < {rps_down}")
            return "scale_down"
        
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
            # Calculate new count
            if action == "scale_up":
                new_count = min(current_count + 1, self.MAX_SERVERS)
            else:  # scale_down
                new_count = max(current_count - 1, self.MIN_SERVERS)
            
            if new_count == current_count:
                log(f"[{service}] No change in server count, skipping")
                return False
            
            log(f"[{service}] Horizontal scaling: {current_count} → {new_count} servers")
            
            # Update service configuration
            project = ProjectDeployer(self.project)
            project.update_service(service, servers_count=new_count)
            
            # Deploy changes (no build, just infrastructure changes)
            success = project.deploy(env=env, service=service, build=False)
            
            if success:
                # Record action with direction for cooldown tracking
                self._record_scale_action(service, f"horizontal_{action}")
                log(f"[{service}] ✓ Horizontal scaling complete")
            else:
                log(f"[{service}] ✗ Horizontal scaling deployment failed")
            
            return success
            
        except Exception as e:
            log(f"[{service}] ✗ Horizontal scaling failed: {e}")
            return False
    
    # ========================================
    # VERTICAL SCALING (CPU/Memory-based)
    # ========================================
    
    def should_scale_vertically(
        self,
        service: str,
        env: str,
        current_cpu: int,
        current_memory: int,
        avg_metrics: Dict[str, float],
        thresholds: Dict[str, float]
    ) -> Optional[Dict[str, Any]]:
        """
        Determine if vertical scaling is needed based on CPU/Memory usage.
        
        Args:
            service: Service name
            env: Environment
            current_cpu: Current CPU count
            current_memory: Current memory in MB
            avg_metrics: Averaged metrics including 'avg_cpu', 'avg_memory'
            thresholds: Scaling thresholds
            
        Returns:
            {"cpu": int, "memory": int, "direction": str} or None
        """
        # Check cooldown
        if not self._can_scale(service, "vertical"):
            log(f"[{service}] Vertical scaling in cooldown period")
            return None
        
        # Get metrics
        avg_cpu = avg_metrics.get('avg_cpu')
        avg_memory = avg_metrics.get('avg_memory')
        
        # Validate data
        if avg_cpu is None or avg_memory is None:
            log(f"[{service}] Missing CPU/Memory data, skipping vertical scaling")
            return None
        
        # Get thresholds
        cpu_up = thresholds.get('cpu_scale_up', 75)
        cpu_down = thresholds.get('cpu_scale_down', 20)
        mem_up = thresholds.get('memory_scale_up', 80)
        mem_down = thresholds.get('memory_scale_down', 30)
        
        # Scale up: EITHER CPU OR Memory is high
        if avg_cpu > cpu_up or avg_memory > mem_up:
            new_specs = self._get_next_tier(current_cpu, current_memory, "up")
            
            if new_specs is None:
                log(f"[{service}] Already at highest tier, cannot scale up")
                return None
            
            log(f"[{service}] Vertical scale UP needed: "
                f"CPU={avg_cpu:.1f}% (>{cpu_up}%) Memory={avg_memory:.1f}% (>{mem_up}%) "
                f"→ {new_specs['cpu']}vCPU/{new_specs['memory']}MB")
            
            return {**new_specs, "direction": "up"}
        
        # Scale down: BOTH CPU AND Memory are low
        elif avg_cpu < cpu_down and avg_memory < mem_down:
            new_specs = self._get_next_tier(current_cpu, current_memory, "down")
            
            if new_specs is None:
                log(f"[{service}] Already at lowest tier, cannot scale down")
                return None
            
            log(f"[{service}] Vertical scale DOWN possible: "
                f"CPU={avg_cpu:.1f}% (<{cpu_down}%) Memory={avg_memory:.1f}% (<{mem_down}%) "
                f"→ {new_specs['cpu']}vCPU/{new_specs['memory']}MB")
            
            return {**new_specs, "direction": "down"}
        
        return None
    
    def execute_vertical_scale(
        self,
        service: str,
        env: str,
        new_specs: Dict[str, Any]
    ) -> bool:
        """
        Execute vertical scaling action.
        
        Args:
            service: Service name
            env: Environment
            new_specs: {"cpu": int, "memory": int, "direction": str}
            
        Returns:
            True if successful
        """
        try:
            cpu = new_specs['cpu']
            memory = new_specs['memory']
            direction = new_specs.get('direction', 'unknown')
            
            log(f"[{service}] Vertical scaling: {cpu}vCPU/{memory}MB")
            
            # Update service configuration
            project = ProjectDeployer(self.project)
            project.update_service(
                service,
                server_cpu=cpu,
                server_memory=memory
            )
            
            # Deploy changes (creates new servers with new specs, then destroys old ones)
            success = project.deploy(env=env, service=service, build=False)
            
            if success:
                # Record action with direction for cooldown tracking
                self._record_scale_action(service, f"vertical_{direction}")
                log(f"[{service}] ✓ Vertical scaling complete")
            else:
                log(f"[{service}] ✗ Vertical scaling deployment failed")
            
            return success
            
        except Exception as e:
            log(f"[{service}] ✗ Vertical scaling failed: {e}")
            return False
    
    # ========================================
    # TIER MANAGEMENT
    # ========================================
    
    def _get_next_tier(
        self,
        current_cpu: int,
        current_memory: int,
        direction: str
    ) -> Optional[Dict[str, int]]:
        """
        Get next size tier based on current specs and direction.
        
        Args:
            current_cpu: Current CPU count
            current_memory: Current memory in MB
            direction: "up" or "down"
            
        Returns:
            {"cpu": int, "memory": int} or None if already at boundary
        """
        current_spec = (current_cpu, current_memory)
        
        try:
            # Find current tier
            current_idx = self.SIZE_TIERS.index(current_spec)
            
            # Calculate next tier
            if direction == "up":
                next_idx = current_idx + 1
                if next_idx >= len(self.SIZE_TIERS):
                    return None  # Already at highest tier
            else:  # down
                next_idx = current_idx - 1
                if next_idx < 0:
                    return None  # Already at lowest tier
            
            cpu, memory = self.SIZE_TIERS[next_idx]
            return {"cpu": cpu, "memory": memory}
            
        except ValueError:
            # Current spec not in standard tiers, find closest
            log(f"Current spec ({current_cpu}vCPU/{current_memory}MB) not in standard tiers, finding closest")
            
            closest_idx = min(
                range(len(self.SIZE_TIERS)),
                key=lambda i: abs(self.SIZE_TIERS[i][1] - current_memory)
            )
            
            # Move in the desired direction from closest tier
            if direction == "up":
                next_idx = min(closest_idx + 1, len(self.SIZE_TIERS) - 1)
            else:
                next_idx = max(closest_idx - 1, 0)
            
            # Check if we actually moved
            if next_idx == closest_idx:
                return None
            
            cpu, memory = self.SIZE_TIERS[next_idx]
            return {"cpu": cpu, "memory": memory}
    
    # ========================================
    # COOLDOWN MANAGEMENT
    # ========================================
    
    def _can_scale(self, service: str, scale_type: str) -> bool:
        """
        Check if scaling action is allowed (cooldown check).
        
        Args:
            service: Service name
            scale_type: "horizontal" or "vertical"
        
        Returns:
            True if enough time has elapsed since last scaling action
        """
        if service not in self.last_scale_action:
            return True
        
        # Determine which action keys to check
        if scale_type == "horizontal":
            keys_to_check = ["horizontal_up", "horizontal_down"]
        elif scale_type == "vertical":
            keys_to_check = ["vertical_up", "vertical_down"]
        else:
            log(f"Unknown scale_type: {scale_type}")
            return False
        
        # Check if any related action is in cooldown
        now = datetime.now()
        
        for key in keys_to_check:
            if key not in self.last_scale_action[service]:
                continue
            
            last_action_time = self.last_scale_action[service][key]
            elapsed = (now - last_action_time).total_seconds()
            
            # Determine cooldown based on direction
            if "_up" in key or "up" in key:
                cooldown = self.SCALE_UP_COOLDOWN
            else:  # _down
                cooldown = self.SCALE_DOWN_COOLDOWN
            
            if elapsed < cooldown:
                remaining = cooldown - elapsed
                log(f"[{service}] {key} in cooldown: {remaining:.0f}s remaining")
                return False
        
        return True
    
    def _record_scale_action(self, service: str, action_key: str):
        """
        Record scaling action timestamp for cooldown tracking.
        
        Args:
            service: Service name
            action_key: Action identifier (e.g., "horizontal_up", "vertical_down")
        """
        if service not in self.last_scale_action:
            self.last_scale_action[service] = {}
        
        self.last_scale_action[service][action_key] = datetime.now()
        log(f"[{service}] Recorded action: {action_key}")