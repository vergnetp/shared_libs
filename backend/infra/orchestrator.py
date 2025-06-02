"""
Main Orchestrator

Central coordination system that ties together all components of the
Personal Cloud Orchestration System. Handles CSV-driven infrastructure
management, deployment workflows, and operational procedures.
"""

import os
import json
import csv
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# Import all our modules
from .infrastructure_state import InfrastructureState
from .managers.digitalocean_manager import DigitalOceanManager
from .managers.ssh_key_manager import SSHKeyManager
from .managers.secret_manager import SecretManager, DockerSecretManager
from .environment_generator import EnvironmentGenerator
from .managers.deployment_manager import DeploymentManager
from .managers.load_balancer_manager import LoadBalancerManager
from .managers.snapshot_manager import SnapshotManager
from .distributed_health import DistributedHealthMonitor


class InfrastructureOrchestrator:
    """
    Main orchestrator that coordinates all infrastructure operations
    """
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        
        # Load configurations
        self.projects_csv = self.config_dir / "projects.csv"
        self.infrastructure_json = self.config_dir / "infrastructure.json"
        self.deployment_config_json = self.config_dir / "deployment_config.json"
        self.email_config_json = self.config_dir / "email_config.json"
        
        # Initialize core components
        self.state = InfrastructureState(str(self.infrastructure_json))
        self.do_manager = DigitalOceanManager()
        self.ssh_manager = SSHKeyManager(do_manager=self.do_manager.manager)
        
        # Initialize secret management
        self.secret_manager = SecretManager(use_vault=False)  # Start with OS env vars
        self.docker_secret_manager = DockerSecretManager(self.secret_manager)
        
        # Initialize other managers
        self.env_generator = EnvironmentGenerator(self.state, self.docker_secret_manager)
        self.load_balancer_manager = LoadBalancerManager(self.state, self.ssh_manager)
        self.snapshot_manager = SnapshotManager(self.do_manager, self.state)
        
        # Will be initialized when deployment config is loaded
        self.deployment_manager = None
        
        # Email integration (placeholder - integrate with your emailer)
        self.emailer = None  # TODO: Initialize with your emailer module
        
    def initialize_system(self) -> Dict[str, Any]:
        """Initialize the entire orchestration system"""
        
        print("🚀 Initializing Personal Cloud Orchestration System")
        
        results = {
            'ssh_keys': self._setup_ssh_keys(),
            'deployment_config': self._load_deployment_config(),
            'csv_validation': self._validate_projects_csv(),
            'system_ready': False
        }
        
        if all([results['ssh_keys']['success'], 
                results['deployment_config']['success'],
                results['csv_validation']['success']]):
            results['system_ready'] = True
            print("✅ System initialization completed successfully")
        else:
            print("❌ System initialization failed - check individual component results")
        
        return results
    
    def _setup_ssh_keys(self) -> Dict[str, Any]:
        """Setup SSH keys for infrastructure access"""
        
        try:
            print("🔑 Setting up SSH keys...")
            
            # Ensure SSH key is ready and uploaded to DigitalOcean
            key_id = self.ssh_manager.ensure_key_ready()
            
            if key_id:
                return {
                    'success': True,
                    'key_id': key_id,
                    'key_path': self.ssh_manager.get_private_key_path()
                }
            else:
                return {
                    'success': False,
                    'error': 'Failed to setup SSH keys'
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _load_deployment_config(self) -> Dict[str, Any]:
        """Load deployment configuration"""
        
        try:
            if not self.deployment_config_json.exists():
                # Create default deployment config
                default_config = {
                    "deployment_platform": "docker",
                    "projects": {}
                }
                
                self.deployment_config_json.parent.mkdir(parents=True, exist_ok=True)
                with open(self.deployment_config_json, 'w') as f:
                    json.dump(default_config, f, indent=2)
            
            with open(self.deployment_config_json, 'r') as f:
                deployment_config = json.load(f)
            
            # Initialize deployment manager
            self.deployment_manager = DeploymentManager(
                self.state, self.env_generator, self.ssh_manager, deployment_config
            )
            
            return {
                'success': True,
                'platform': deployment_config.get('deployment_platform', 'docker'),
                'projects': len(deployment_config.get('projects', {}))
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _validate_projects_csv(self) -> Dict[str, Any]:
        """Validate projects CSV file"""
        
        try:
            if not self.projects_csv.exists():
                # Create example CSV
                self.projects_csv.parent.mkdir(parents=True, exist_ok=True)
                with open(self.projects_csv, 'w', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['Project', 'Servers', 'MasterSpec', 'WebSpec'])
                    writer.writerow(['example', '1', 's-1vcpu-1gb', 's-1vcpu-1gb'])
                
                return {
                    'success': True,
                    'message': 'Created example projects.csv - please configure your projects'
                }
            
            # Validate CSV structure
            with open(self.projects_csv, 'r') as f:
                reader = csv.DictReader(f)
                projects = list(reader)
            
            if not projects:
                return {
                    'success': False,
                    'error': 'No projects found in CSV'
                }
            
            # Validate required columns
            required_columns = ['Project', 'Servers', 'MasterSpec', 'WebSpec']
            for project in projects:
                for col in required_columns:
                    if col not in project:
                        return {
                            'success': False,
                            'error': f'Missing required column: {col}'
                        }
            
            return {
                'success': True,
                'projects': len(projects),
                'project_names': [p['Project'] for p in projects]
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def orchestrate_infrastructure(self, force_recreate: bool = False) -> Dict[str, Any]:
        """Main orchestration function - create infrastructure from CSV"""
        
        print("🏗️  Starting infrastructure orchestration from CSV")
        
        try:
            # Load projects from CSV
            projects = self._load_projects_csv()
            
            # Plan infrastructure changes
            infrastructure_plan = self._plan_infrastructure_changes(projects, force_recreate)
            
            print(f"📋 Infrastructure plan: {infrastructure_plan['summary']}")
            
            if not infrastructure_plan['changes_needed']:
                print("✅ No infrastructure changes needed")
                return {
                    'success': True,
                    'message': 'Infrastructure already matches desired state',
                    'plan': infrastructure_plan
                }
            
            # Execute infrastructure changes
            execution_results = self._execute_infrastructure_plan(infrastructure_plan)
            
            if execution_results['success']:
                # Deploy load balancer configuration
                lb_result = self.load_balancer_manager.deploy_nginx_config()
                execution_results['load_balancer'] = lb_result
                
                print("✅ Infrastructure orchestration completed successfully")
            else:
                print("❌ Infrastructure orchestration failed")
            
            return execution_results
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _load_projects_csv(self) -> List[Dict[str, Any]]:
        """Load and parse projects CSV"""
        
        with open(self.projects_csv, 'r') as f:
            reader = csv.DictReader(f)
            return list(reader)
    
    def _plan_infrastructure_changes(self, projects: List[Dict[str, Any]], 
                                   force_recreate: bool = False) -> Dict[str, Any]:
        """Plan what infrastructure changes are needed"""
        
        current_droplets = self.state.get_all_droplets()
        current_projects = self.state.get_all_projects()
        
        plan = {
            'droplets_to_create': [],
            'droplets_to_destroy': [],
            'droplets_to_resize': [],
            'services_to_deploy': [],
            'services_to_remove': [],
            'changes_needed': False,
            'summary': {}
        }
        
        # Calculate required infrastructure
        required_droplets = {'master': {'size': 's-2vcpu-4gb', 'region': 'lon1', 'role': 'master'}}
        required_services = {}
        
        for project in projects:
            project_name = project['Project']
            server_count = int(project['Servers'])
            web_spec = project['WebSpec']
            
            # Add web droplets if multiple servers needed
            if server_count > 1:
                for i in range(1, server_count):
                    droplet_name = f"web{i}"
                    required_droplets[droplet_name] = {
                        'size': web_spec,
                        'region': 'lon1',
                        'role': 'web'
                    }
            
            # Plan services for each environment
            for env in ['prod', 'uat', 'test']:
                project_key = f"{project_name}-{env}"
                
                # Determine droplet assignments
                if server_count == 1:
                    # Small project - everything on master
                    assigned_droplets = ['master']
                else:
                    # Distribute across web droplets
                    web_droplets = [f"web{i}" for i in range(1, server_count)]
                    assigned_droplets = web_droplets
                
                # Calculate hash-based ports
                backend_port = self.state.get_hash_based_port(project_name, env, 8000, 1000)
                frontend_port = self.state.get_hash_based_port(project_name, env, 9000, 1000)
                db_port = self.state.get_hash_based_port(project_name, env, 5000, 1000)
                redis_port = self.state.get_hash_based_port(project_name, env, 6000, 1000)
                opensearch_port = self.state.get_hash_based_port(project_name, env, 9000, 1000)
                vault_port = self.state.get_hash_based_port(project_name, env, 8000, 1000)
                
                required_services[project_key] = {
                    'backend': {'port': backend_port, 'assigned_droplets': assigned_droplets[:2]},
                    'frontend': {'port': frontend_port, 'assigned_droplets': assigned_droplets[:2]},
                    'database': {'port': db_port, 'assigned_droplets': [assigned_droplets[0]]},
                    'redis': {'port': redis_port, 'assigned_droplets': [assigned_droplets[0]]},
                    'opensearch': {'port': opensearch_port, 'assigned_droplets': ['master']},
                    'vault': {'port': vault_port, 'assigned_droplets': ['master']}
                }
        
        # Compare with current state
        
        # Check droplets
        for name, config in required_droplets.items():
            current_droplet = current_droplets.get(name)
            
            if not current_droplet or force_recreate:
                plan['droplets_to_create'].append({'name': name, 'config': config})
                plan['changes_needed'] = True
            elif current_droplet['size'] != config['size']:
                plan['droplets_to_resize'].append({
                    'name': name,
                    'current_size': current_droplet['size'],
                    'new_size': config['size']
                })
                plan['changes_needed'] = True
        
        # Check for droplets to remove
        for name in current_droplets:
            if name not in required_droplets:
                plan['droplets_to_destroy'].append(name)
                plan['changes_needed'] = True
        
        # Check services
        for project_key, services in required_services.items():
            current_project_services = current_projects.get(project_key, {})
            
            for service_type, service_config in services.items():
                current_service = current_project_services.get(service_type)
                
                if (not current_service or 
                    current_service.get('port') != service_config['port'] or
                    current_service.get('assigned_droplets') != service_config['assigned_droplets']):
                    
                    plan['services_to_deploy'].append({
                        'project': project_key,
                        'service_type': service_type,
                        'config': service_config
                    })
                    plan['changes_needed'] = True
        
        # Summary
        plan['summary'] = {
            'droplets_to_create': len(plan['droplets_to_create']),
            'droplets_to_destroy': len(plan['droplets_to_destroy']),
            'droplets_to_resize': len(plan['droplets_to_resize']),
            'services_to_deploy': len(plan['services_to_deploy']),
            'total_required_droplets': len(required_droplets),
            'total_required_services': sum(len(services) for services in required_services.values())
        }
        
        return plan
    
    def _execute_infrastructure_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the infrastructure plan"""
        
        results = {
            'success': True,
            'droplet_operations': [],
            'service_operations': [],
            'errors': []
        }
        
        try:
            # 1. Create new droplets
            for droplet_info in plan['droplets_to_create']:
                print(f"📦 Creating droplet: {droplet_info['name']}")
                
                result = self._create_droplet(droplet_info['name'], droplet_info['config'])
                results['droplet_operations'].append(result)
                
                if not result['success']:
                    results['success'] = False
                    results['errors'].append(f"Failed to create droplet {droplet_info['name']}")
            
            # 2. Resize existing droplets
            for resize_info in plan['droplets_to_resize']:
                print(f"📏 Resizing droplet: {resize_info['name']}")
                
                result = self._resize_droplet(resize_info['name'], resize_info['new_size'])
                results['droplet_operations'].append(result)
                
                if not result['success']:
                    results['success'] = False
                    results['errors'].append(f"Failed to resize droplet {resize_info['name']}")
            
            # 3. Deploy services
            for service_info in plan['services_to_deploy']:
                print(f"🚀 Configuring service: {service_info['project']}-{service_info['service_type']}")
                
                result = self._configure_service(
                    service_info['project'],
                    service_info['service_type'],
                    service_info['config']
                )
                results['service_operations'].append(result)
                
                if not result['success']:
                    results['success'] = False
                    results['errors'].append(f"Failed to configure service {service_info['project']}-{service_info['service_type']}")
            
            # 4. Destroy old droplets (be careful here!)
            for droplet_name in plan['droplets_to_destroy']:
                print(f"🗑️  Destroying droplet: {droplet_name}")
                
                result = self._destroy_droplet(droplet_name)
                results['droplet_operations'].append(result)
                
                if not result['success']:
                    results['errors'].append(f"Failed to destroy droplet {droplet_name}")
                    # Don't mark as failure - this is cleanup
            
        except Exception as e:
            results['success'] = False
            results['errors'].append(f"Execution error: {str(e)}")
        
        return results
    
    def _create_droplet(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new droplet"""
        
        try:
            # Get SSH key ID
            ssh_key_id = self.ssh_manager.get_digitalocean_key_id()
            
            # Create droplet
            droplet = self.do_manager.create_droplet(
                name=name,
                size=config['size'],
                region=config['region'],
                ssh_keys=[ssh_key_id] if ssh_key_id else []
            )
            
            # Add to infrastructure state
            self.state.add_droplet(
                name=name,
                ip=droplet.ip_address,
                size=config['size'],
                region=config['region'],
                role=config['role']
            )
            
            # Setup monitoring relationships
            self._setup_monitoring_relationships(name, config['role'])
            
            return {
                'success': True,
                'operation': 'create_droplet',
                'name': name,
                'ip': droplet.ip_address
            }
            
        except Exception as e:
            return {
                'success': False,
                'operation': 'create_droplet',
                'name': name,
                'error': str(e)
            }
    
    def _resize_droplet(self, name: str, new_size: str) -> Dict[str, Any]:
        """Resize an existing droplet"""
        
        try:
            # This would involve creating a snapshot, creating new droplet, and migrating
            # For now, just update the state - actual resize would be more complex
            
            droplet_config = self.state.get_droplet(name)
            if droplet_config:
                self.state.add_droplet(
                    name=name,
                    ip=droplet_config['ip'],
                    size=new_size,
                    region=droplet_config['region'],
                    role=droplet_config['role'],
                    monitors=droplet_config.get('monitors', [])
                )
            
            return {
                'success': True,
                'operation': 'resize_droplet',
                'name': name,
                'new_size': new_size,
                'note': 'State updated - actual resize requires manual intervention'
            }
            
        except Exception as e:
            return {
                'success': False,
                'operation': 'resize_droplet',
                'name': name,
                'error': str(e)
            }
    
    def _configure_service(self, project: str, service_type: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Configure a service in the infrastructure state"""
        
        try:
            self.state.add_project_service(
                project=project,
                service_type=service_type,
                port=config.get('port'),
                assigned_droplets=config['assigned_droplets'],
                service_config={'type': config.get('type', 'web')}
            )
            
            return {
                'success': True,
                'operation': 'configure_service',
                'project': project,
                'service_type': service_type,
                'assigned_droplets': config['assigned_droplets']
            }
            
        except Exception as e:
            return {
                'success': False,
                'operation': 'configure_service',
                'project': project,
                'service_type': service_type,
                'error': str(e)
            }
    
    def _destroy_droplet(self, name: str) -> Dict[str, Any]:
        """Destroy a droplet"""
        
        try:
            # Remove from DigitalOcean
            success = self.do_manager.destroy_droplet(name)
            
            if success:
                # Remove from infrastructure state
                self.state.remove_droplet(name)
                
                return {
                    'success': True,
                    'operation': 'destroy_droplet',
                    'name': name
                }
            else:
                return {
                    'success': False,
                    'operation': 'destroy_droplet',
                    'name': name,
                    'error': 'DigitalOcean destroy operation failed'
                }
                
        except Exception as e:
            return {
                'success': False,
                'operation': 'destroy_droplet',
                'name': name,
                'error': str(e)
            }
    
    def _setup_monitoring_relationships(self, droplet_name: str, role: str):
        """Setup peer monitoring relationships for new droplet"""
        
        all_droplets = self.state.get_all_droplets()
        
        if role == 'master':
            # Master monitors all web droplets
            web_droplets = [name for name, config in all_droplets.items() if config['role'] == 'web']
            self.state.add_droplet(
                name=droplet_name,
                ip=all_droplets[droplet_name]['ip'],
                size=all_droplets[droplet_name]['size'],
                region=all_droplets[droplet_name]['region'],
                role=role,
                monitors=web_droplets
            )
            
            # Update web droplets to monitor master
            for web_name in web_droplets:
                web_config = all_droplets[web_name]
                current_monitors = web_config.get('monitors', [])
                if droplet_name not in current_monitors:
                    current_monitors.append(droplet_name)
                    
                    self.state.add_droplet(
                        name=web_name,
                        ip=web_config['ip'],
                        size=web_config['size'],
                        region=web_config['region'],
                        role=web_config['role'],
                        monitors=current_monitors
                    )
        
        elif role == 'web':
            # Web droplet monitors master and one other web droplet (ring topology)
            master_droplets = [name for name, config in all_droplets.items() if config['role'] == 'master']
            web_droplets = [name for name, config in all_droplets.items() if config['role'] == 'web' and name != droplet_name]
            
            monitors = []
            if master_droplets:
                monitors.append(master_droplets[0])
            if web_droplets:
                monitors.append(web_droplets[0])  # Monitor first web droplet for ring topology
            
            self.state.add_droplet(
                name=droplet_name,
                ip=all_droplets[droplet_name]['ip'],
                size=all_droplets[droplet_name]['size'],
                region=all_droplets[droplet_name]['region'],
                role=role,
                monitors=monitors
            )
    
    # Deployment operations
    def deploy_to_uat(self, project: str, branch: str = "main") -> Dict[str, Any]:
        """Deploy project to UAT environment"""
        
        if not self.deployment_manager:
            return {'success': False, 'error': 'Deployment manager not initialized'}
        
        print(f"🚀 Deploying {project} to UAT from branch {branch}")
        
        try:
            result = self.deployment_manager.deploy_to_uat(project, branch)
            
            if result['status'] == 'success':
                # Update load balancer after deployment
                lb_result = self.load_balancer_manager.deploy_nginx_config()
                result['load_balancer_update'] = lb_result
            
            return result
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def deploy_to_prod(self, project: str, use_uat_tag: bool = True) -> Dict[str, Any]:
        """Deploy project to production environment"""
        
        if not self.deployment_manager:
            return {'success': False, 'error': 'Deployment manager not initialized'}
        
        print(f"🚀 Deploying {project} to production")
        
        try:
            result = self.deployment_manager.deploy_to_prod(project, use_uat_tag)
            
            if result['status'] == 'success':
                # Update load balancer after deployment
                lb_result = self.load_balancer_manager.deploy_nginx_config()
                result['load_balancer_update'] = lb_result
            
            return result
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def scale_project(self, project: str, target_servers: int) -> Dict[str, Any]:
        """Scale a project to target number of servers"""
        
        print(f"📈 Scaling {project} to {target_servers} servers")
        
        try:
            # Update CSV with new server count
            self._update_project_csv(project, target_servers)
            
            # Re-orchestrate infrastructure
            result = self.orchestrate_infrastructure()
            
            return {
                'success': result['success'],
                'project': project,
                'target_servers': target_servers,
                'orchestration_result': result
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def _update_project_csv(self, project: str, new_server_count: int):
        """Update project server count in CSV"""
        
        # Read current CSV
        projects = []
        with open(self.projects_csv, 'r') as f:
            reader = csv.DictReader(f)
            projects = list(reader)
        
        # Update target project
        for proj in projects:
            if proj['Project'] == project:
                proj['Servers'] = str(new_server_count)
                break
        
        # Write back to CSV
        with open(self.projects_csv, 'w', newline='') as f:
            if projects:
                writer = csv.DictWriter(f, fieldnames=projects[0].keys())
                writer.writeheader()
                writer.writerows(projects)
    
    # Health monitoring operations
    async def start_health_monitoring(self, droplet_name: str) -> Dict[str, Any]:
        """Start health monitoring daemon on a specific droplet"""
        
        try:
            # Initialize health monitor for this droplet
            health_monitor = DistributedHealthMonitor(
                droplet_name=droplet_name,
                infrastructure_state=self.state,
                snapshot_manager=self.snapshot_manager,
                load_balancer_manager=self.load_balancer_manager,
                emailer=self.emailer
            )
            
            print(f"🔍 Starting health monitoring on {droplet_name}")
            
            # Start monitoring (this will run indefinitely)
            await health_monitor.start_monitoring()
            
            return {
                'success': True,
                'droplet': droplet_name,
                'status': 'monitoring_started'
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_infrastructure_status(self) -> Dict[str, Any]:
        """Get comprehensive infrastructure status"""
        
        try:
            # Get basic infrastructure summary
            summary = self.state.get_summary()
            
            # Get DigitalOcean status
            do_summary = self.do_manager.get_infrastructure_summary()
            
            # Get load balancer status
            lb_status = self.load_balancer_manager.get_load_balancer_status()
            
            # Get snapshot summary
            snapshot_summary = self.snapshot_manager.get_snapshot_summary()
            
            # Validate infrastructure state
            validation_issues = self.state.validate_state()
            
            return {
                'timestamp': datetime.now().isoformat(),
                'infrastructure_summary': summary,
                'digitalocean_status': do_summary,
                'load_balancer_status': lb_status,
                'snapshot_summary': snapshot_summary,
                'validation_issues': validation_issues,
                'overall_health': 'healthy' if not validation_issues else 'issues_detected'
            }
            
        except Exception as e:
            return {
                'error': str(e),
                'overall_health': 'error'
            }
    
    def emergency_recovery(self, failed_droplet: str) -> Dict[str, Any]:
        """Perform emergency recovery of a failed droplet"""
        
        print(f"🚨 Starting emergency recovery for {failed_droplet}")
        
        try:
            # Create emergency snapshot if droplet still exists
            emergency_snapshot = self.snapshot_manager.create_emergency_snapshot(
                failed_droplet, "manual_recovery"
            )
            
            # Attempt snapshot recovery
            recovery_result = self.snapshot_manager.recover_droplet_from_snapshot(failed_droplet)
            
            if recovery_result['success']:
                # Update load balancer
                lb_result = self.load_balancer_manager.deploy_nginx_config()
                recovery_result['load_balancer_update'] = lb_result
                
                print(f"✅ Emergency recovery completed for {failed_droplet}")
            else:
                print(f"❌ Emergency recovery failed for {failed_droplet}")
            
            return {
                'recovery_result': recovery_result,
                'emergency_snapshot': emergency_snapshot
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def cleanup_infrastructure(self, dry_run: bool = True) -> Dict[str, Any]:
        """Clean up old snapshots and unused resources"""
        
        print(f"🧹 {'Dry run: ' if dry_run else ''}Cleaning up infrastructure")
        
        results = {
            'snapshots_cleaned': 0,
            'validation_run': False,
            'actions_taken': []
        }
        
        try:
            if not dry_run:
                # Clean up old deployment snapshots
                for droplet_name in self.state.get_all_droplets().keys():
                    self.snapshot_manager.cleanup_old_deployment_snapshots(droplet_name, keep=3)
                    results['snapshots_cleaned'] += 1
                    results['actions_taken'].append(f"Cleaned snapshots for {droplet_name}")
                
                # Clean up emergency snapshots older than 7 days
                self.snapshot_manager.cleanup_emergency_snapshots(older_than_days=7)
                results['actions_taken'].append("Cleaned old emergency snapshots")
            
            # Validate snapshots still exist
            validation_result = self.snapshot_manager.validate_snapshots()
            results['validation_run'] = True
            results['validation_result'] = validation_result
            
            if validation_result.get('invalid_snapshots', 0) > 0:
                results['actions_taken'].append(f"Removed {validation_result['invalid_snapshots']} invalid snapshot references")
            
            return results
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def update_administrator_ip(self, new_ip: str) -> Dict[str, Any]:
        """Update administrator IP across all infrastructure"""
        
        try:
            # Get current admin IP
            old_ip = os.getenv('ADMIN_IP', 'unknown')
            
            # Update DigitalOcean firewall rules
            self.do_manager.update_administrator_ip(old_ip, new_ip)
            
            # Update environment variable (for this session)
            os.environ['ADMIN_IP'] = new_ip
            
            print(f"🔧 Updated administrator IP from {old_ip} to {new_ip}")
            
            return {
                'success': True,
                'old_ip': old_ip,
                'new_ip': new_ip,
                'message': 'Administrator IP updated across all droplets'
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_service_discovery_info(self, project: str, environment: str) -> Dict[str, Any]:
        """Get service discovery information for debugging"""
        
        try:
            discovery_info = self.env_generator.get_service_discovery_info(project, environment)
            
            # Add load balancer information
            lb_summary = self.load_balancer_manager._get_upstream_summary()
            
            return {
                'project': project,
                'environment': environment,
                'services': discovery_info,
                'load_balancer_upstreams': lb_summary,
                'nginx_config_location': '/opt/app/nginx.conf'
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }


# CLI interface for the orchestrator
def main():
    """Main CLI interface"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Personal Cloud Orchestration System')
    parser.add_argument('--init', action='store_true', help='Initialize the system')
    parser.add_argument('--orchestrate', action='store_true', help='Orchestrate infrastructure from CSV')
    parser.add_argument('--status', action='store_true', help='Get infrastructure status')
    parser.add_argument('--deploy-uat', metavar='PROJECT', help='Deploy project to UAT')
    parser.add_argument('--deploy-prod', metavar='PROJECT', help='Deploy project to production')
    parser.add_argument('--scale', nargs=2, metavar=('PROJECT', 'SERVERS'), help='Scale project to N servers')
    parser.add_argument('--monitor', metavar='DROPLET', help='Start health monitoring on droplet')
    parser.add_argument('--recover', metavar='DROPLET', help='Emergency recovery of failed droplet')
    parser.add_argument('--cleanup', action='store_true', help='Clean up old resources')
    parser.add_argument('--update-ip', metavar='NEW_IP', help='Update administrator IP')
    parser.add_argument('--reproduce', metavar='TAG', help='Reproduce deployment from tag')
    parser.add_argument('--local', action='store_true', help='Use local codebase instead of git clone')
    parser.add_argument('--project-path', metavar='PATH', help='Path to local project directory')
    parser.add_argument('--reproduce-dir', metavar='DIR', help='Directory for reproduced code')
    parser.add_argument('--force', action='store_true', help='Force recreate resources')
    parser.add_argument('--dry-run', action='store_true', help='Dry run (show what would be done)')
    
    args = parser.parse_args()
    
    # Initialize orchestrator
    orchestrator = InfrastructureOrchestrator()
    
    if args.init:
        result = orchestrator.initialize_system()
        print(json.dumps(result, indent=2))
    
    elif args.orchestrate:
        result = orchestrator.orchestrate_infrastructure(force_recreate=args.force)
        print(json.dumps(result, indent=2))
    
    elif args.status:
        result = orchestrator.get_infrastructure_status()
        print(json.dumps(result, indent=2))
    
    elif args.deploy_uat:
        result = orchestrator.deploy_to_uat(
            args.deploy_uat, 
            use_local=args.local,
            local_project_path=args.project_path
        )
        print(json.dumps(result, indent=2))
    
    elif args.deploy_prod:
        result = orchestrator.deploy_to_prod(args.deploy_prod)
        print(json.dumps(result, indent=2))
    
    elif args.scale:
        project, servers = args.scale
        result = orchestrator.scale_project(project, int(servers))
        print(json.dumps(result, indent=2))
    
    elif args.monitor:
        asyncio.run(orchestrator.start_health_monitoring(args.monitor))
    
    elif args.recover:
        result = orchestrator.emergency_recovery(args.recover)
        print(json.dumps(result, indent=2))
    
    elif args.cleanup:
        result = orchestrator.cleanup_infrastructure(dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
    
    elif args.update_ip:
        result = orchestrator.update_administrator_ip(args.update_ip)
        print(json.dumps(result, indent=2))
    
    elif args.reproduce:
        result = orchestrator.deployment_manager.reproduce_deployment(
            args.reproduce, 
            args.reproduce_dir
        )
        print(json.dumps(result, indent=2))
    
    else:
        parser.print_help()

if __name__ == '__main__':
    main()