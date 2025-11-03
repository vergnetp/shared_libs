"""
Auto-Scaling Coordinator - Manages auto-scaling logic for HealthMonitor.

This module separates auto-scaling concerns from health monitoring,
providing a clean interface for metrics collection and scaling decisions.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta

try:
    from .deployment_config import DeploymentConfigurer
except ImportError:
    from deployment_config import DeploymentConfigurer
try:
    from .live_deployment_query import LiveDeploymentQuery
except ImportError:
    from live_deployment_query import LiveDeploymentQuery
try:
    from .metrics_collector import MetricsCollector
except ImportError:
    from metrics_collector import MetricsCollector
try:
    from .auto_scaler import AutoScaler
except ImportError:
    from auto_scaler import AutoScaler
try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .do_manager import DOManager
except ImportError:
    from do_manager import DOManager
try:
    from .health_monitor import HealthMonitor
except ImportError:
    from health_monitor import HealthMonitor


def log(msg):
    Logger.log(msg)


class AutoScalingCoordinator:
    """
    Coordinates auto-scaling operations for all services.
    
    Responsibilities:
    - Collect metrics from all deployed services
    - Check if services need scaling
    - Execute scaling decisions via AutoScaler
    - Maintain cooldowns and check intervals
    
    Used by HealthMonitor's leader server to manage auto-scaling.
    """
    
    # Check scaling every 5 minutes (not every monitor cycle)
    AUTO_SCALE_CHECK_INTERVAL = 300  # 5 minutes
    
    # Averaging window for stable scaling decisions
    METRICS_WINDOW_MINUTES = 10  # 10 minutes
    
    # Threshold key names (short form)
    DEFAULT_THRESHOLDS = {
        "cpu_scale_up": 75,
        "cpu_scale_down": 20,
        "memory_scale_up": 80,
        "memory_scale_down": 30,
        "rps_scale_up": 500,
        "rps_scale_down": 50,
    }
    
    def __init__(self):
        """Initialize coordinator with singleton collectors"""
        self._metrics_collector = MetricsCollector()
        self._auto_scalers = {}  # {project: AutoScaler}
        self._last_check = {}  # {project_env_service: timestamp}
    
    # ========================================
    # METRICS COLLECTION
    # ========================================
    
    def collect_all_metrics(self) -> None:
        """
        Collect metrics from all deployed services across all servers.
        
        This should be called every monitor cycle (every 60s) to maintain
        a continuous history of metrics for averaging.
        
        Called by: All servers (leader and followers)
        
        MIGRATED: Now uses LiveDeploymentQuery to get actual running containers
        instead of reading from JSON.
        """      
        try:
            # Get all running containers across all servers
            all_containers = LiveDeploymentQuery.get_all_running_containers()
            
            for server_ip, containers in all_containers.items():
                for container_name in containers:
                    # Parse container name: {project}_{env}_{service}[_secondary]
                    parts = container_name.split('_')
                    if len(parts) < 3:
                        continue
                    
                    project = parts[0]
                    env = parts[1]
                    service_name = parts[2]  # Ignore _secondary suffix if present
                    
                    # Collect service-specific metrics
                    metrics = MetricsCollector.collect_service_metrics(
                        project, env, service_name, server_ip
                    )
                    
                    if metrics:
                        # Store with unique key
                        key = f"{server_ip}_{project}_{env}_{service_name}"
                        self._metrics_collector.store_metrics(key, metrics)
        
        except Exception as e:
            log(f"Error collecting metrics: {e}")
    
    # ========================================
    # SCALING ORCHESTRATION
    # ========================================
    
    def check_and_scale_all_services(self) -> None:
        """
        Check all services and perform auto-scaling if needed.
        
        Called by: Leader server only
        
        Process:
        1. Iterate through all deployed services
        2. Check if auto-scaling is enabled
        3. Collect and average metrics
        4. Make scaling decisions
        5. Execute scaling if needed
        
        MIGRATED: Now uses LiveDeploymentQuery to discover services
        instead of reading from JSON.
        """        
        if DOManager.is_infrastructure_locked():
            log("Infrastructure modification in progress (healing), skipping auto-scaling this cycle")
            return
        
        # NEW: Acquire infrastructure lock
        leader_ip = HealthMonitor.get_my_ip()
        if not DOManager.acquire_infrastructure_lock(leader_ip):
            log("Failed to acquire infrastructure lock for auto-scaling")
            return
        
        try:
            # ===== EXISTING AUTO-SCALING LOGIC (NO CHANGES) =====
            
            log("Checking auto-scaling for all services...")
            
            try:
                # Get summary of all running services
                summary = LiveDeploymentQuery.get_deployment_summary(None)  # All projects
                
                # Group by project
                projects_envs = {}
                for server_ip in summary['servers']:
                    services = LiveDeploymentQuery.get_services_on_server(server_ip)
                    for svc in services:
                        project = svc['project']
                        env = svc['env']
                        
                        if project not in projects_envs:
                            projects_envs[project] = set()
                        projects_envs[project].add(env)
                
                # Process each project/env
                for project, envs in projects_envs.items():
                    # Load project config
                    try:
                        config = DeploymentConfigurer(project)
                    except Exception as e:
                        log(f"Could not load config for project {project}: {e}")
                        continue
                    
                    # Get or create AutoScaler for this project
                    scaler = self._get_auto_scaler(project)
                    
                    for env in envs:
                        services = config.get_services(env)
                        
                        for service_name, service_config in services.items():
                            # Check if this service is actually running
                            if LiveDeploymentQuery.is_service_running(project, env, service_name):
                                self._check_service_scaling(
                                    project, env, service_name,
                                    service_config, scaler
                                )
            
            except Exception as e:
                log(f"Error in auto-scaling check: {e}")
        
        finally:
            # NEW: ALWAYS release lock, even if auto-scaling failed
            DOManager.release_infrastructure_lock(leader_ip)
            log("Auto-scaling complete - infrastructure lock released")




    def _check_service_scaling(
            self,
            project: str,
            env: str,
            service: str,
            service_config: Dict[str, Any],
            scaler: AutoScaler
        ) -> None:
            """
            Check and scale a single service.
            
            Args:
                project: Project name
                env: Environment
                service: Service name
                service_config: Service configuration dict
                scaler: AutoScaler instance for this project
                
            MIGRATED: Now uses LiveDeploymentQuery to get servers
            instead of reading from JSON.
            """
            # Get auto_scaling config
            auto_scale_config = service_config.get("auto_scaling")
            
            # Check if enabled
            if not auto_scale_config:
                return
            
            # Normalize config
            if auto_scale_config is True:
                # Enable both with defaults
                auto_scale_config = {"enabled": True}
            elif isinstance(auto_scale_config, dict):
                auto_scale_config["enabled"] = True
            else:
                return
            
            # Infer type from config
            has_vertical = "vertical" in auto_scale_config and auto_scale_config["vertical"]
            has_horizontal = "horizontal" in auto_scale_config and auto_scale_config["horizontal"]
            
            # If neither specified, enable both (defaults)
            if not has_vertical and not has_horizontal:
                has_vertical = has_horizontal = True
            
            # Check if enough time has passed since last check
            check_key = f"{project}_{env}_{service}"
            if not self._should_check_now(check_key):
                return
            
            # Get servers running this service (LIVE QUERY)            
            servers = LiveDeploymentQuery.get_servers_running_service(project, env, service)
            
            if not servers:
                return
            
            log(f"Auto-scaling check for {project}/{env}/{service} ({len(servers)} servers)")
            
            # Collect and average metrics from all servers
            aggregated_metrics = self._aggregate_metrics(
                project, env, service, servers
            )
            
            if not aggregated_metrics:
                log(f"  No metrics available for {service}, skipping")
                return
            
            log(f"  Metrics: CPU={aggregated_metrics['avg_cpu']:.1f}% "
                f"Memory={aggregated_metrics['avg_memory']:.1f}% "
                f"RPS={aggregated_metrics.get('avg_rps', 0):.1f}")
            
            # Get thresholds
            vertical_thresholds = self._get_vertical_thresholds(auto_scale_config) if has_vertical else None
            horizontal_thresholds = self._get_horizontal_thresholds(auto_scale_config) if has_horizontal else None
            
            # PRIORITY 1: Check vertical scaling (resource-based)
            if has_vertical:
                if self._try_vertical_scaling(
                    project, env, service, service_config,
                    aggregated_metrics, vertical_thresholds, scaler
                ):
                    self._record_check(check_key)
                    return  # Don't check horizontal in same cycle
            
            # PRIORITY 2: Check horizontal scaling (traffic-based)
            if has_horizontal:
                if self._try_horizontal_scaling(
                    project, env, service, servers,
                    aggregated_metrics, horizontal_thresholds, scaler
                ):
                    self._record_check(check_key)

    # ========================================
    # METRICS AGGREGATION
    # ========================================
    
    def _aggregate_metrics(
        self,
        project: str,
        env: str,
        service: str,
        servers: list
    ) -> Optional[Dict[str, float]]:
        """
        Aggregate metrics from all servers running a service.
        
        Args:
            project: Project name
            env: Environment
            service: Service name
            servers: List of server IPs
            
        Returns:
            Aggregated metrics dict or None if insufficient data
        """
        all_metrics = []
        
        for server_ip in servers:
            key = f"{server_ip}_{project}_{env}_{service}"
            avg_metrics = self._metrics_collector.get_average_metrics(
                key, 
                window_minutes=self.METRICS_WINDOW_MINUTES
            )
            
            if avg_metrics:
                all_metrics.append(avg_metrics)
        
        if not all_metrics:
            return None
        
        # Calculate overall averages
        return {
            'avg_cpu': sum(m['avg_cpu'] for m in all_metrics) / len(all_metrics),
            'avg_memory': sum(m['avg_memory'] for m in all_metrics) / len(all_metrics),
            'avg_rps': sum(m['avg_rps'] for m in all_metrics) / len(all_metrics),
        }
    
    # ========================================
    # SCALING EXECUTION
    # ========================================
    
    def _try_vertical_scaling(
        self,
        project: str,
        env: str,
        service: str,
        service_config: Dict[str, Any],
        metrics: Dict[str, float],
        thresholds: Dict[str, float],
        scaler: AutoScaler
    ) -> bool:
        """
        Try vertical scaling if needed.
        
        Returns:
            True if scaling was executed (success or failure)
        """
        current_cpu = service_config.get("server_cpu", 1)
        current_memory = service_config.get("server_memory", 1024)
        
        new_specs = scaler.should_scale_vertically(
            service, env, current_cpu, current_memory,
            metrics, thresholds
        )
        
        if not new_specs:
            return False
        
        log(f"  Triggering vertical scaling for {service}")
        success = scaler.execute_vertical_scale(service, env, new_specs)
        
        if success:
            log(f"  ✓ Vertical scaling completed for {service}")
        else:
            log(f"  ✗ Vertical scaling failed for {service}")
        
        return True  # Attempted scaling
    
    def _try_horizontal_scaling(
        self,
        project: str,
        env: str,
        service: str,
        servers: list,
        metrics: Dict[str, float],
        thresholds: Dict[str, float],
        scaler: AutoScaler
    ) -> bool:
        """
        Try horizontal scaling if needed.
        
        Returns:
            True if scaling was executed (success or failure)
        """
        action = scaler.should_scale_horizontally(
            service, env, len(servers),
            metrics, thresholds
        )
        
        if not action:
            return False
        
        log(f"  Triggering horizontal {action} for {service}")
        success = scaler.execute_horizontal_scale(
            service, env, action, len(servers)
        )
        
        if success:
            log(f"  ✓ Horizontal scaling completed for {service}")
        else:
            log(f"  ✗ Horizontal scaling failed for {service}")
        
        return True  # Attempted scaling
    
    # ========================================
    # THRESHOLD MANAGEMENT
    # ========================================
    
    def _get_vertical_thresholds(self, auto_scale_config: Dict[str, Any]) -> Dict[str, float]:
        """Get vertical scaling thresholds with defaults"""
        config_thresholds = auto_scale_config.get("vertical", {})
        
        # Only merge CPU/Memory defaults
        defaults = {
            k: v for k, v in self.DEFAULT_THRESHOLDS.items()
            if 'cpu' in k or 'memory' in k
        }
        
        return {**defaults, **config_thresholds}
    
    def _get_horizontal_thresholds(self, auto_scale_config: Dict[str, Any]) -> Dict[str, float]:
        """Get horizontal scaling thresholds with defaults"""
        config_thresholds = auto_scale_config.get("horizontal", {})
        
        # Only merge RPS defaults
        defaults = {
            k: v for k, v in self.DEFAULT_THRESHOLDS.items()
            if 'rps' in k
        }
        
        return {**defaults, **config_thresholds}
    
    # ========================================
    # CHECK INTERVAL MANAGEMENT
    # ========================================
    
    def _should_check_now(self, check_key: str) -> bool:
        """
        Check if enough time has passed since last scaling check.
        
        Args:
            check_key: Unique key for service (project_env_service)
            
        Returns:
            True if should check now
        """
        if check_key not in self._last_check:
            return True
        
        last_check = self._last_check[check_key]
        elapsed = (datetime.now() - last_check).total_seconds()
        
        return elapsed >= self.AUTO_SCALE_CHECK_INTERVAL
    
    def _record_check(self, check_key: str) -> None:
        """Record that we checked this service"""
        self._last_check[check_key] = datetime.now()
    
    # ========================================
    # AUTO_SCALER MANAGEMENT
    # ========================================
    
    def _get_auto_scaler(self, project: str) -> AutoScaler:
        """Get or create AutoScaler for a project"""
        if project not in self._auto_scalers:
            self._auto_scalers[project] = AutoScaler(project)
        
        return self._auto_scalers[project]