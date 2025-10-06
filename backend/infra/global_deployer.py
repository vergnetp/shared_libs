# unified_deployer.py - Simple public API for single-zone and multi-zone deployments

from typing import List, Dict, Any, Optional
from deployment_config import DeploymentConfigurer
from server_inventory import ServerInventory
from deployer import Deployer
from logger import Logger
from concurrent.futures import ThreadPoolExecutor, as_completed
import env_loader
from rollback_manager import RollbackManager

def log(msg):
    Logger.log(msg)


class UnifiedDeployer:
    """
    Unified deployment interface supporting both single-zone and multi-zone deployments.
    
    Simple public API:
    - build(env, push=True) - Build images once, push to registry
    - deploy(env, zones=None, service=None, build=True, parallel=True) - Deploy anywhere
    
    Usage:
        deployer = UnifiedDeployer("myapp")
        
        # Single zone (automatic)
        deployer.deploy(env="prod")
        
        # Multi-zone (automatic detection)
        deployer.deploy(env="prod", zones=["lon1", "nyc1", "sgp1"])
        
        # Or just build
        deployer.build(env="prod", push=True)
    """
    
    def __init__(self, project: str, auto_sync: bool = True):
        """
        Initialize deployer for a project.
        
        Args:
            project: Project name
            auto_sync: Whether to auto-sync config/data during deployments
        """
        self.project = project
        self.auto_sync = auto_sync
        self.configurer = DeploymentConfigurer(project)
        log(f"Initialized deployer for project: {project}")
    
    # =========================================================================
    # PUBLIC API - Simple interface
    # =========================================================================
    
    def build(self, env: str = None, push: bool = True) -> bool:
        """
        Build Docker images for all services.
        
        Args:
            env: Environment to build (default: all environments)
            push: Push images to registry (required for multi-zone)
            
        Returns:
            True if build successful
            
        Example:
            deployer.build(env="prod", push=True)
        """
        log(f"Building images for {self.project}/{env or 'all'}")
        Logger.start()
        
        deployer = Deployer(self.project, auto_sync=False)
        success = deployer.build_images(environment=env, push_to_registry=push)
        
        Logger.end()
        status = "✓ Build complete" if success else "✗ Build failed"
        log(status)
        
        return success
    
    def deploy(
        self,
        env: str,
        zones: List[str] = None,
        service: str = None,
        build: bool = True,
        parallel: bool = True
    ) -> Dict[str, bool]:
        """
        Deploy services - automatically handles single-zone or multi-zone.

        Notes: 
            For multi-zone, you need an account with Cloudflare that has Load Balancer enabled ($5/month).
            Add CLOUDFLARE_API_TOKEN in the environment variables.
            If not found or LB not supported in Cloudflare, will use the first zone in the list.
                    
        Args:
            env: Environment to deploy (e.g., "prod", "dev")
            zones: Target zones (e.g., ["lon1", "nyc3"], as per DigitalOcean naming). If None, auto-detects from config
            service: Deploy specific service only (optional, all otherwise)
            build: Whether to build images before deploying. Default to True.
            parallel: Whether to deploy zones in parallel (faster, default) or sequentially (safer)
            
        Returns:
            Dict mapping zone -> success/failure
            
        Examples:
            # Auto-detect zones from config
            deployer.deploy(env="prod")
            
            # Specific zones
            deployer.deploy(env="prod", zones=["lon1", "nyc3", "sgp1"])
            
            # Specific service across zones
            deployer.deploy(env="prod", service="api", zones=["lon1", "nyc3"])
            
            # Sequential deployment (easier debugging)
            deployer.deploy(env="prod", parallel=False)
        """
        log(f"Starting deployment: {self.project}/{env}")
        Logger.start()
        
        # Auto-detect zones from config if not specified
        target_zones = zones or self._get_configured_zones(env, service)
        
        if not target_zones:
            log("No zones configured - check your project config")
            Logger.end()
            return {}
        
        # Determine deployment strategy
        is_multi_zone = len(target_zones) > 1
        
        if is_multi_zone:
            log(f"Multi-zone deployment: {target_zones}")
            results = self._deploy_multi_zone(
                env, target_zones, service, build, parallel
            )
        else:
            log(f"Single-zone deployment: {target_zones[0]}")
            results = self._deploy_single_zone(
                env, target_zones[0], service, build
            )
        
        # Summary
        self._print_deployment_summary(results)
        
        Logger.end()
        return results
    
    def status(self, env: str = None) -> Dict[str, Any]:
        """
        Get deployment status across all zones.
        
        Args:
            env: Filter by environment (optional)
            
        Returns:
            Dict with zone-level status information
            
        Example:
            status = deployer.status(env="prod")
            # {'lon1': {'green': 2, 'blue': 0, 'reserve': 1}, ...}
        """
        log("Gathering deployment status...")
        Logger.start()
        
        ServerInventory.sync_with_digitalocean()
        servers = ServerInventory.list_all_servers()
        
        zone_summary = {}
        for server in servers:
            zone = server['zone']
            status = server['deployment_status']
            
            if zone not in zone_summary:
                zone_summary[zone] = {
                    'green': 0,
                    'blue': 0,
                    'reserve': 0,
                    'destroying': 0,
                    'total': 0
                }
            
            zone_summary[zone][status] = zone_summary[zone].get(status, 0) + 1
            zone_summary[zone]['total'] += 1
        
        Logger.end()
        return zone_summary
    
    def rollback(self, env: str, service: str, version: str = None) -> bool:
        """
        Rollback service to previous deployment.
        
        Args:
            env: Environment
            service: Service name
            version: Target version (default: previous)
            
        Returns:
            True if rollback successful
            
        Example:
            deployer.rollback(env="prod", service="api")
            deployer.rollback(env="prod", service="api", version="v1.2.3")
        """
        log(f"Initiating rollback for {self.project}/{env}/{service}")
        Logger.start()
        
        success = RollbackManager.rollback(
            self.project, 
            env, 
            service, 
            target_version=version,
            validate_registry=True
        )
        
        Logger.end()
        
        if success:
            log(f"✓ Rollback complete for {service}")
        else:
            log(f"✗ Rollback failed for {service}")
        
        return success


    # =========================================================================
    # INTERNAL IMPLEMENTATION
    # =========================================================================
    
    def _deploy_single_zone(
        self,
        env: str,
        zone: str,
        service: str,
        build: bool
    ) -> Dict[str, bool]:
        """Deploy to a single zone using standard Deployer"""
        deployer = Deployer(self.project, auto_sync=self.auto_sync)
        
        success = deployer.deploy(
            env=env,
            service_name=service,
            build=build
        )
        
        return {zone: success}
    
    def _deploy_multi_zone(
        self,
        env: str,
        zones: List[str],
        service: str,
        build: bool,
        parallel: bool
    ) -> Dict[str, bool]:
        """Deploy to multiple zones"""
        
        # Check if Cloudflare LB is available
        import os
        cloudflare_api_token = os.getenv("CLOUDFLARE_API_TOKEN")
        
        if not cloudflare_api_token:
            log("WARNING: Multi-zone deployment without CLOUDFLARE_API_TOKEN")
            log("         Falling back to single-zone deployment (first zone only)")
            zones = [zones[0]]
        elif not self._check_cloudflare_lb_available(cloudflare_api_token):
            log("WARNING: Cloudflare Load Balancer not enabled on your account")
            log("         Multi-zone deployment requires Load Balancer ($5/month)")
            log("         Enable at: https://dash.cloudflare.com/?to=/:account/traffic/load-balancing")
            log("         Falling back to single-zone deployment (first zone only)")
            zones = [zones[0]]
        
        # Build once for all zones
        if build:
            log("Building images for all zones...")
            if not self.build(env=env, push=True):
                log("Build failed - aborting deployment")
                return {zone: False for zone in zones}
        
        # Deploy to each zone
        if parallel:
            results = self._deploy_parallel(env, zones, service)
        else:
            results = self._deploy_sequential(env, zones, service)
        
        # Setup global load balancing if multi-zone
        if len(zones) > 1 and cloudflare_api_token:
            self._setup_global_load_balancing(env, zones, service, cloudflare_api_token, results)
        
        return results
    
    def _deploy_parallel(
        self,
        env: str,
        zones: List[str],
        service: Optional[str]
    ) -> Dict[str, bool]:
        """Deploy to zones in parallel"""
        log(f"Deploying to {len(zones)} zones in parallel...")
        
        results = {}
        
        with ThreadPoolExecutor(max_workers=min(len(zones), 5)) as executor:
            futures = {
                executor.submit(
                    self._deploy_to_zone,
                    env, zone, service
                ): zone
                for zone in zones
            }
            
            for future in as_completed(futures):
                zone = futures[future]
                try:
                    success = future.result()
                    results[zone] = success
                    status = "✓" if success else "✗"
                    log(f"{status} Zone {zone}: {'completed' if success else 'failed'}")
                except Exception as e:
                    log(f"✗ Zone {zone} exception: {e}")
                    results[zone] = False
        
        return results
    
    def _deploy_sequential(
        self,
        env: str,
        zones: List[str],
        service: Optional[str]
    ) -> Dict[str, bool]:
        """Deploy to zones sequentially"""
        log(f"Deploying to {len(zones)} zones sequentially...")
        
        results = {}
        
        for zone in zones:
            log(f"\n{'='*60}")
            log(f"Deploying to zone: {zone}")
            log(f"{'='*60}")
            Logger.start()
            
            try:
                success = self._deploy_to_zone(env, zone, service)
                results[zone] = success
                
                status = "✓" if success else "✗"
                log(f"{status} Zone {zone}: {'completed' if success else 'failed'}")
                
            except Exception as e:
                log(f"✗ Zone {zone} exception: {e}")
                results[zone] = False
            
            Logger.end()
        
        return results
    
    def _deploy_to_zone(
        self,
        env: str,
        zone: str,
        service: Optional[str]
    ) -> bool:
        """Deploy to a specific zone"""
        try:
            services = self._filter_services_for_zone(env, zone, service)
            
            if not services:
                log(f"No services configured for zone {zone}")
                return True
            
            log(f"Deploying {len(services)} service(s) to {zone}")
            
            deployer = Deployer(self.project, auto_sync=self.auto_sync)
            
            for svc_name, svc_config in services.items():
                log(f"  → {svc_name} in {zone}")
                
                success = deployer.deploy(
                    env=env,
                    service_name=svc_name,
                    build=False  # Already built globally
                )
                
                if not success:
                    log(f"Failed to deploy {svc_name} in {zone}")
                    return False
            
            return True
            
        except Exception as e:
            log(f"Exception deploying to {zone}: {e}")
            return False
    
    def _get_configured_zones(self, env: str, service: Optional[str] = None) -> List[str]:
        """Extract zones from service configurations"""
        zones = set()
        services = self.configurer.get_services(env)
        
        for svc_name, svc_config in services.items():
            if service and svc_name != service:
                continue
            
            zone = svc_config.get("server_zone")
            if zone:
                zones.add(zone)
        
        return sorted(list(zones))
    
    def _filter_services_for_zone(
        self,
        env: str,
        zone: str,
        service: Optional[str] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Get services for a specific zone"""
        services = self.configurer.get_services(env)
        
        filtered = {}
        for svc_name, svc_config in services.items():
            if service and svc_name != service:
                continue
            
            if svc_config.get("server_zone") == zone:
                filtered[svc_name] = svc_config
        
        return filtered
    
    def _check_cloudflare_lb_available(self, cloudflare_api_token: str) -> bool:
        """
        Check if Cloudflare Load Balancer is enabled on the account.
        
        Returns:
            True if LB is available, False otherwise
        """
        try:
            # Try to list load balancer pools (requires LB subscription)
            from execute_cmd import CommandExecuter
            import json
            
            # First get account ID
            cmd = (
                f'curl -sS -X GET "https://api.cloudflare.com/client/v4/accounts" '
                f'-H "Authorization: Bearer {cloudflare_api_token}" '
                f'-H "Content-Type: application/json"'
            )
            
            result = CommandExecuter.run_cmd(cmd, "localhost")
            data = json.loads(str(result))
            
            if not data.get("success") or not data.get("result"):
                log("Could not fetch Cloudflare account info")
                return False
            
            account_id = data["result"][0]["id"]
            
            # Try to access load balancer pools endpoint
            cmd = (
                f'curl -sS -X GET "https://api.cloudflare.com/client/v4/accounts/{account_id}/load_balancers/pools" '
                f'-H "Authorization: Bearer {cloudflare_api_token}" '
                f'-H "Content-Type: application/json"'
            )
            
            result = CommandExecuter.run_cmd(cmd, "localhost")
            data = json.loads(str(result))
            
            # If success=true, LB is enabled
            # If error about subscription, LB not enabled
            if data.get("success"):
                return True
            
            # Check for subscription error
            errors = data.get("errors", [])
            for error in errors:
                if "subscription" in str(error).lower() or "upgrade" in str(error).lower():
                    return False
            
            # If we got here with errors, assume LB not available
            return False
            
        except Exception as e:
            log(f"Error checking Cloudflare LB availability: {e}")
            return False
    
    def _setup_global_load_balancing(
        self,
        env: str,
        zones: List[str],
        service: Optional[str],
        cloudflare_api_token: str,
        deployment_results: Dict[str, bool]
    ):
        """
        Setup Cloudflare Load Balancer for multi-zone deployment.
        Only sets up for successfully deployed zones.
        """
        from nginx_config_generator import NginxConfigGenerator
        import json
        
        # Get service config
        services = self.configurer.get_services(env)
        
        # If specific service, only setup LB for that service
        if service:
            services = {service: services.get(service)}
        
        for svc_name, svc_config in services.items():
            domain = svc_config.get("domain")
            if not domain:
                continue
            
            # Only setup LB for services that have multiple zones
            service_zones = self._filter_services_for_zone(env, None, svc_name)
            if len(service_zones) <= 1:
                continue
            
            # Collect nginx IPs from successfully deployed zones
            nginx_ips = []
            for zone in zones:
                if not deployment_results.get(zone):
                    log(f"Skipping {zone} for LB (deployment failed)")
                    continue
                
                # Get nginx IP for this zone
                servers = ServerInventory.get_servers(
                    deployment_status=ServerInventory.STATUS_GREEN,
                    zone=zone
                )
                
                if servers:
                    nginx_ips.append(servers[0]['ip'])
            
            if len(nginx_ips) < 2:
                log(f"Not enough zones deployed successfully for {domain} LB")
                continue
            
            log(f"Setting up Cloudflare Load Balancer for {domain}")
            log(f"  Origin IPs: {nginx_ips}")
            
            try:
                NginxConfigGenerator.setup_cloudflare_load_balancer(
                    domain=domain,
                    origin_ips=nginx_ips,
                    cloudflare_api_token=cloudflare_api_token,
                    geo_steering=True
                )
                log(f"✓ Load Balancer configured for {domain}")
            except Exception as e:
                log(f"✗ Failed to setup Load Balancer for {domain}: {e}")
    
    def _print_deployment_summary(self, results: Dict[str, bool]):
        """Print deployment summary"""
        successful = [z for z, success in results.items() if success]
        failed = [z for z, success in results.items() if not success]
        
        log(f"\nDeployment summary:")
        log(f"  ✓ Successful: {len(successful)}/{len(results)} zones")
        if successful:
            log(f"    {', '.join(successful)}")
        if failed:
            log(f"  ✗ Failed: {', '.join(failed)}")

    def list_deployments(self, env: str = None) -> Dict[str, Any]:
        """
        Show current deployment status.
        
        Args:
            env: Filter by environment
            
        Returns:
            Deployment status dictionary
        """
        deployer = Deployer(self.project, auto_sync=False)
        return deployer.list_deployments(env)


    def print_deployments(self, env: str = None):
        """
        Pretty-print deployment status to console.
        
        Args:
            env: Filter by environment
        """
        deployer = Deployer(self.project, auto_sync=False)
        deployer.print_deployments(env)


    def logs(
        self,
        service: str,
        env: str,
        lines: int = 100
    ) -> str:
        """
        Fetch logs from service containers.
        
        Args:
            service: Service name
            env: Environment
            lines: Number of lines to tail
            
        Returns:
            Log output
        """
        deployer = Deployer(self.project, auto_sync=False)
        return deployer.logs(service, env, lines)


    def print_logs(
        self,
        service: str,
        env: str,
        lines: int = 100
    ):
        """
        Fetch and print logs to console.
        
        Args:
            service: Service name
            env: Environment  
            lines: Number of lines to tail
        """
        deployer = Deployer(self.project, auto_sync=False)
        deployer.print_logs(service, env, lines)
                        
def main():
    """Simple CLI for unified deployer"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Unified deployment CLI')
    parser.add_argument('--project', required=True, help='Project name')
    parser.add_argument('--env', required=True, help='Environment')
    
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # Build
    build_cmd = subparsers.add_parser('build', help='Build images')
    build_cmd.add_argument('--no-push', action='store_true', help='Skip registry push')
    
    # Deploy
    deploy_cmd = subparsers.add_parser('deploy', help='Deploy services')
    deploy_cmd.add_argument('--zones', nargs='+', help='Target zones')
    deploy_cmd.add_argument('--service', help='Specific service')
    deploy_cmd.add_argument('--no-build', action='store_true', help='Skip build')
    deploy_cmd.add_argument('--sequential', action='store_true', help='Sequential deployment')

    # Status command
    status_cmd = subparsers.add_parser('status', help='Show deployment status')
    status_cmd.add_argument('--env', help='Filter by environment')
    status_cmd.add_argument('--json', action='store_true', help='Output as JSON')

    # Logs command
    logs_cmd = subparsers.add_parser('logs', help='Fetch service logs')
    logs_cmd.add_argument('--service', required=True, help='Service name')
    logs_cmd.add_argument('--env', required=True, help='Environment')
    logs_cmd.add_argument('--lines', type=int, default=100, help='Number of lines')

    # Rollback
    build_cmd = subparsers.add_parser('rollback', help='Rollback deployment')
    parser.add_argument('--env', required=True, help='Environment')
    deploy_cmd.add_argument('--service', help='Specific service')
    deploy_cmd.add_argument('--version', help='To specific version (default to latest)')
    
    args = parser.parse_args()
    
    deployer = UnifiedDeployer(args.project)
    
    if args.command == 'build':
        success = deployer.build(env=args.env, push=not args.no_push)
        exit(0 if success else 1)
    
    elif args.command == 'deploy':
        results = deployer.deploy(
            env=args.env,
            zones=args.zones,
            service=args.service,
            build=not args.no_build,
            parallel=not args.sequential
        )
        exit(0 if all(results.values()) else 1)
    
    elif args.command == 'status':
        if args.json:
            import json
            status = deployer.list_deployments(env=args.env)
            print(json.dumps(status, indent=2))
        else:
            deployer.print_deployments(env=args.env)

    elif args.command == 'logs':
        deployer.print_logs(
            service=args.service,
            env=args.env,
            lines=args.lines
        )

    elif args.command == 'rollback':
        success = deployer.rollback(env=args.env, service=not args.service, version=args.version)
        exit(0 if success else 1)

if __name__ == "__main__":
    main()