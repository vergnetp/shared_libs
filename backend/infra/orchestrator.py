"""
Main Orchestrator

Central coordination system that ties together all components of the
Personal Cloud Orchestration System. Handles JSON-driven infrastructure
management, deployment workflows, and operational procedures with
transactional orchestration.
"""

import os
import json
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# Import all our modules
from .infrastructure_state import InfrastructureState
from .managers.digitalocean_manager import DigitalOceanManager
from .managers.ssh_key_manager import SSHKeyManager
from .managers.secret_manager import SecretManager, ContainerSecretManager
from .environment_generator import EnvironmentGenerator
from .managers.deployment_manager import DeploymentManager
from .managers.load_balancer_manager import LoadBalancerManager
from .managers.snapshot_manager import SnapshotManager
from .distributed_health import DistributedHealthMonitor
from ..emailing import Emailer, EmailConfig

class InfrastructureOrchestrator:
   """
   Main orchestrator that coordinates all infrastructure operations with transactional support
   """
   
   def __init__(self, config_dir: str = "config"):
       self.config_dir = Path(config_dir)
       
       # Load configurations (no more CSV!)
       self.infrastructure_json = self.config_dir / "infrastructure.json"
       self.deployment_config_json = self.config_dir / "deployment_config.json"
       self.email_config_json = self.config_dir / "email_config.json"
       
       # Initialize core components
       self.state = InfrastructureState(str(self.infrastructure_json))
       self.do_manager = DigitalOceanManager()
       self.ssh_manager = SSHKeyManager(do_manager=self.do_manager.manager)
       
       # Initialize secret management
       self.secret_manager = SecretManager(use_vault=False)  # Start with OS env vars
       self.container_secret_manager = ContainerSecretManager(self.secret_manager)
       
       # Initialize other managers
       self.env_generator = EnvironmentGenerator(self.state, self.container_secret_manager)
       self.load_balancer_manager = LoadBalancerManager(self.state, self.ssh_manager)
       self.snapshot_manager = SnapshotManager(self.do_manager, self.state)
       
       # Will be initialized when deployment config is loaded
       self.deployment_manager = None
       
       # Email integration
       email_config_path = self.config_dir / "email_config.json"
       if email_config_path.exists():            
           with open(email_config_path, 'r') as f:
               email_config_data = json.load(f)
           email_config = EmailConfig(**email_config_data)
           self.emailer = Emailer(email_config)
       else:
           self.emailer = None
       
   def initialize_system(self) -> Dict[str, Any]:
       """Initialize the entire orchestration system"""
       
       print("🚀 Initializing Personal Cloud Orchestration System")
       
       results = {
           'ssh_keys': self._setup_ssh_keys(),
           'deployment_config': self._load_deployment_config(),
           'infrastructure_spec': self._validate_infrastructure_spec(),
           'system_ready': False
       }
       
       if all([results['ssh_keys']['success'], 
               results['deployment_config']['success'],
               results['infrastructure_spec']['success']]):
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
           
           # Set snapshot manager reference for post-deployment snapshots
           self.deployment_manager.snapshot_manager = self.snapshot_manager
           
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
   
   def _validate_infrastructure_spec(self) -> Dict[str, Any]:
       """Validate infrastructure configuration (no longer needs spec section)"""
       
       try:
           droplets = self.state.get_all_droplets()
           projects = self.state.get_all_projects()
           
           if not droplets:
               # Create default minimal setup
               self.state.add_droplet(
                   name="master",
                   ip=None,  # Will be set when created
                   size="s-2vcpu-4gb",
                   region="lon1",
                   role="master"
               )
               
               return {
                   'success': True,
                   'message': 'Created default master droplet - please run --orchestrate to create infrastructure'
               }
           
           # Count projects and environments
           project_count = 0
           environment_count = 0
           for project, environments in self.state.state["projects"].items():
               project_count += 1
               environment_count += len(environments)
           
           return {
               'success': True,
               'droplets': len(droplets),
               'projects': project_count,
               'environments': environment_count
           }
           
       except Exception as e:
           return {
               'success': False,
               'error': str(e)
           }
   
   def orchestrate_infrastructure(self, force_recreate: bool = False, skip_confirmation: bool = False) -> Dict[str, Any]:
       """Main orchestration function - transactional create-before-destroy approach"""
       
       print("🏗️  Starting transactional infrastructure orchestration")
       
       try:
           # Phase 1: Analyze current state vs desired state
           analysis = self._analyze_infrastructure_state()
           
           if not analysis['changes_needed']:
               print("✅ Infrastructure already matches desired state")
               return {
                   'success': True,
                   'message': 'No changes needed',
                   'analysis': analysis
               }
           
           # Phase 2: Validate changes and get user confirmation
           if not skip_confirmation:
               validation = self._validate_infrastructure_changes(analysis)
               
               if not validation['valid'] or not validation['user_confirmed']:
                   return {
                       'success': False,
                       'message': 'Operation cancelled or validation failed',
                       'validation': validation
                   }
           
           # Phase 3: Plan the transactional execution
           execution_plan = self._plan_transactional_execution(analysis, force_recreate)
           
           # Phase 4: Execute the plan with rollback capability
           execution_results = self._execute_transactional_plan(execution_plan)
           
           if execution_results['success']:
               # Phase 5: Update load balancer and finalize
               print("🔄 Updating load balancer configuration...")
               lb_result = self.load_balancer_manager.deploy_nginx_config()
               execution_results['load_balancer'] = lb_result
               
               if lb_result.get('success'):
                   print("✅ Transactional orchestration completed successfully")
               else:
                   print("⚠️  Orchestration completed but load balancer update failed")
                   execution_results['warnings'] = ['Load balancer update failed']
           else:
               print("❌ Transactional orchestration failed")
           
           return execution_results
           
       except Exception as e:
           return {
               'success': False,
               'error': str(e)
           }
   
   def _analyze_infrastructure_state(self) -> Dict[str, Any]:
       """Analyze current vs desired state and determine what changes are needed"""
       
       print("🔍 Analyzing infrastructure state...")
       
       # Get state comparisons
       to_create = self.state.get_droplets_to_create(self.do_manager)
       to_modify = self.state.get_droplets_to_modify(self.do_manager)
       to_delete = self.state.get_droplets_to_delete(self.do_manager)
       ip_corrections = self.state.get_ip_corrections_needed(self.do_manager)
       
       changes_needed = bool(to_create or to_modify or to_delete or ip_corrections)
       
       analysis = {
           'changes_needed': changes_needed,
           'droplets_to_create': to_create,
           'droplets_to_modify': to_modify,
           'droplets_to_delete': to_delete,
           'ip_corrections': ip_corrections,
           'summary': {
               'create_count': len(to_create),
               'modify_count': len(to_modify),
               'delete_count': len(to_delete),
               'ip_correction_count': len(ip_corrections)
           }
       }
       
       # Log the analysis
       if to_create:
           print(f"   📦 {len(to_create)} droplets to create: {[d['name'] for d in to_create]}")
       if to_modify:
           print(f"   🔄 {len(to_modify)} droplets to modify: {[d['name'] for d in to_modify]}")
       if to_delete:
           print(f"   🗑️  {len(to_delete)} droplets to delete: {[d['name'] for d in to_delete]}")
       if ip_corrections:
           print(f"   🔧 {len(ip_corrections)} IP corrections needed")
       
       return analysis
   
   def _validate_infrastructure_changes(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
       """Validate proposed infrastructure changes and get user confirmation"""
       
       print("\n🔍 Infrastructure Change Summary:")
       print("=" * 50)
       
       validation = {
           'valid': True,
           'warnings': [],
           'errors': [],
           'user_confirmed': False
       }
       
       # Show what will happen
       if analysis['droplets_to_create']:
           print(f"✅ CREATE {len(analysis['droplets_to_create'])} droplets:")
           for droplet in analysis['droplets_to_create']:
               config = droplet['config']
               cost_estimate = self._estimate_droplet_cost(config['size'])
               print(f"   • {droplet['name']} ({config['size']}, {config['region']}) - ~${cost_estimate}/month")
       
       if analysis['droplets_to_modify']:
           print(f"🔄 MODIFY {len(analysis['droplets_to_modify'])} droplets:")
           for droplet in analysis['droplets_to_modify']:
               print(f"   • {droplet['name']}: {', '.join(droplet['changes'])}")
               
               # Check for service impact
               services = self.state.get_services_on_droplet(droplet['name'])
               if services:
                   print(f"     Services affected: {[s['service_name'] for s in services]}")
                   validation['warnings'].append(f"Modifying {droplet['name']} will cause brief downtime for {len(services)} services")
       
       if analysis['droplets_to_delete']:
           print(f"🗑️  DELETE {len(analysis['droplets_to_delete'])} droplets:")
           for droplet in analysis['droplets_to_delete']:
               print(f"   • {droplet['name']} (will be destroyed)")
               
               # Check for services that need migration
               services = self.state.get_services_on_droplet(droplet['name'])
               if services:
                   print(f"     Services to migrate: {[s['service_name'] for s in services]}")
                   
                   # Check if migration is possible
                   migration_plan = self.state.plan_service_migration(droplet['name'])
                   if migration_plan['issues']:
                       for issue in migration_plan['issues']:
                           validation['errors'].append(f"Migration issue: {issue}")
       
       if analysis['ip_corrections']:
           print(f"🔧 CORRECT {len(analysis['ip_corrections'])} IP addresses:")
           for correction in analysis['ip_corrections']:
               print(f"   • {correction['name']}: {correction['desired_ip']} → {correction['actual_ip']}")
       
       # Calculate cost impact
       cost_impact = self._calculate_cost_impact(analysis)
       if cost_impact['monthly_change'] != 0:
           print(f"\n💰 Monthly cost change: ${cost_impact['monthly_change']:+.2f}")
           if cost_impact['monthly_change'] > 50:
               validation['warnings'].append(f"Significant cost increase: ${cost_impact['monthly_change']:+.2f}/month")
       
       # Show warnings and errors
       if validation['warnings']:
           print(f"\n⚠️  Warnings:")
           for warning in validation['warnings']:
               print(f"   • {warning}")
       
       if validation['errors']:
           print(f"\n❌ Errors:")
           for error in validation['errors']:
               print(f"   • {error}")
           validation['valid'] = False
           return validation
       
       # Get user confirmation for destructive changes
       destructive_changes = analysis['droplets_to_modify'] + analysis['droplets_to_delete']
       
       if destructive_changes:
           print(f"\n⚠️  This operation will modify/destroy {len(destructive_changes)} droplets.")
           print("Services will be migrated automatically, but brief downtime may occur.")
           
           try:
               response = input("\nProceed with infrastructure changes? [y/N]: ").strip().lower()
               validation['user_confirmed'] = response in ['y', 'yes']
           except (KeyboardInterrupt, EOFError):
               validation['user_confirmed'] = False
           
           if not validation['user_confirmed']:
               print("Operation cancelled by user.")
               validation['valid'] = False
       else:
           # No destructive changes, auto-confirm
           validation['user_confirmed'] = True
       
       print("=" * 50)
       
       return validation
   
   def _estimate_droplet_cost(self, size: str) -> float:
       """Estimate monthly cost for a droplet size"""
       
       # Rough DigitalOcean pricing estimates (as of 2025)
       cost_map = {
           's-1vcpu-1gb': 6.00,
           's-1vcpu-2gb': 12.00,
           's-2vcpu-2gb': 18.00,
           's-2vcpu-4gb': 24.00,
           's-4vcpu-8gb': 48.00,
           's-8vcpu-16gb': 96.00,
           'c-2': 24.00,
           'c-4': 48.00,
           'c-8': 96.00,
           'm-2vcpu-16gb': 96.00,
       }
       
       return cost_map.get(size, 20.00)  # Default estimate
   
   def _calculate_cost_impact(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
       """Calculate monthly cost impact of infrastructure changes"""
       
       cost_impact = {
           'monthly_change': 0.0,
           'new_cost': 0.0,
           'removed_cost': 0.0
       }
       
       # Cost of new droplets
       for droplet in analysis['droplets_to_create']:
           if droplet['reason'] != 'recreation_requested':  # Don't double-count recreations
               cost = self._estimate_droplet_cost(droplet['config']['size'])
               cost_impact['new_cost'] += cost
               cost_impact['monthly_change'] += cost
       
       # Cost of modified droplets (difference between old and new)
       for droplet in analysis['droplets_to_modify']:
           old_cost = self._estimate_droplet_cost(droplet['current_config']['size'])
           new_cost = self._estimate_droplet_cost(droplet['desired_config']['size'])
           cost_impact['monthly_change'] += (new_cost - old_cost)
       
       # Cost savings from deleted droplets
       for droplet in analysis['droplets_to_delete']:
           cost = self._estimate_droplet_cost(droplet['config']['size'])
           cost_impact['removed_cost'] += cost
           cost_impact['monthly_change'] -= cost
       
       return cost_impact
   
   def _plan_transactional_execution(self, analysis: Dict[str, Any], force_recreate: bool) -> Dict[str, Any]:
       """Plan the transactional execution with service migration"""
       
       print("📋 Planning transactional execution...")
       
       plan = {
           'phase_1_create': [],
           'phase_2_migrate': [],
           'phase_3_cleanup': [],
           'rollback_actions': [],
           'service_migrations': {},
           'estimated_downtime_minutes': 0
       }
       
       # Handle IP corrections first (simple, no rollback needed)
       for correction in analysis['ip_corrections']:
           plan['phase_1_create'].append({
               'action': 'correct_ip',
               'droplet': correction['name'],
               'correct_ip': correction['actual_ip']
           })
       
       # Handle new droplets (create only)
       for droplet_info in analysis['droplets_to_create']:
           plan['phase_1_create'].append({
               'action': 'create_droplet',
               'droplet': droplet_info['name'],
               'config': droplet_info['config'],
               'reason': droplet_info['reason']
           })
       
       # Handle modifications (create new, migrate, destroy old)
       for droplet_info in analysis['droplets_to_modify']:
           droplet_name = droplet_info['name']
           temp_name = f"{droplet_name}-new"
           
           # Plan service migration
           migration_plan = self.state.plan_service_migration(droplet_name)
           
           plan['phase_1_create'].append({
               'action': 'create_droplet',
               'droplet': temp_name,
               'config': droplet_info['desired_config'],
               'reason': f"replacement_for_{droplet_name}"
           })
           
           plan['phase_2_migrate'].append({
               'action': 'migrate_services',
               'from_droplet': droplet_name,
               'to_droplet': temp_name,
               'migration_plan': migration_plan
           })
           
           plan['phase_2_migrate'].append({
               'action': 'rename_droplet',
               'from_name': temp_name,
               'to_name': droplet_name
           })
           
           plan['phase_3_cleanup'].append({
               'action': 'destroy_droplet',
               'droplet': droplet_name,
               'reason': 'replaced_by_new_droplet'
           })
           
           plan['service_migrations'][droplet_name] = migration_plan
           
           # Estimate downtime (service migration time)
           plan['estimated_downtime_minutes'] += 2  # ~2 minutes per droplet modification
       
       # Handle deletions (migrate services, then destroy)
       for droplet_info in analysis['droplets_to_delete']:
           droplet_name = droplet_info['name']
           
           # Plan service migration away from this droplet
           migration_plan = self.state.plan_service_migration(droplet_name)
           
           if migration_plan['migrations']:
               plan['phase_2_migrate'].append({
                   'action': 'migrate_services',
                   'from_droplet': droplet_name,
                   'migration_plan': migration_plan
               })
               plan['service_migrations'][droplet_name] = migration_plan
           
           plan['phase_3_cleanup'].append({
               'action': 'destroy_droplet',
               'droplet': droplet_name,
               'reason': 'not_in_desired_state'
           })
       
       # Plan rollback actions for each phase
       for action in plan['phase_1_create']:
           if action['action'] == 'create_droplet':
               plan['rollback_actions'].append({
                   'phase': 1,
                   'action': 'destroy_droplet',
                   'droplet': action['droplet']
               })
       
       print(f"   📊 Execution plan: {len(plan['phase_1_create'])} creates, {len(plan['phase_2_migrate'])} migrations, {len(plan['phase_3_cleanup'])} cleanups")
       print(f"   ⏱️  Estimated downtime: {plan['estimated_downtime_minutes']} minutes")
       
       return plan
   
   def _execute_transactional_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
       """Execute the transactional plan with rollback on failure"""
       
       print("🚀 Executing transactional plan...")
       
       results = {
           'success': True,
           'phase_1_results': [],
           'phase_2_results': [],
           'phase_3_results': [],
           'rollback_performed': False,
           'errors': []
       }
       
       try:
           # Phase 1: Create new resources (can rollback by destroying)
           print("📦 Phase 1: Creating new resources...")
           phase_1_success = self._execute_phase_1_create(plan['phase_1_create'], results)
           
           if not phase_1_success:
               print("❌ Phase 1 failed, rolling back...")
               self._rollback_phase_1(plan['rollback_actions'], results)
               results['rollback_performed'] = True
               results['success'] = False
               return results
           
           # Phase 2: Migrate services (can rollback by reverting)
           print("🔄 Phase 2: Migrating services...")
           phase_2_success = self._execute_phase_2_migrate(plan['phase_2_migrate'], results)
           
           if not phase_2_success:
               print("❌ Phase 2 failed, rolling back...")
               self._rollback_phase_2(plan['phase_2_migrate'], results)
               self._rollback_phase_1(plan['rollback_actions'], results)
               results['rollback_performed'] = True
               results['success'] = False
               return results
           
           # Phase 3: Cleanup old resources (minimal rollback - manual intervention)
           print("🗑️  Phase 3: Cleaning up old resources...")
           phase_3_success = self._execute_phase_3_cleanup(plan['phase_3_cleanup'], results)
           
           if not phase_3_success:
               print("⚠️  Phase 3 had issues, but core migration completed")
               results['errors'].append("Some cleanup operations failed - manual intervention may be needed")
               # Don't rollback phase 3 failures - services are already migrated
           
           print("✅ All phases completed successfully")
           
       except Exception as e:
           results['success'] = False
           results['errors'].append(f"Execution error: {str(e)}")
           print(f"❌ Execution failed: {e}")
       
       return results
   
   def _execute_phase_1_create(self, create_actions: List[Dict[str, Any]], results: Dict[str, Any]) -> bool:
       """Execute Phase 1: Create new droplets and correct IPs"""
       
       for action in create_actions:
           if action['action'] == 'correct_ip':
               # Simple IP correction
               droplet_name = action['droplet']
               correct_ip = action['correct_ip']
               
               self.state.update_droplet_ip(droplet_name, correct_ip)
               
               results['phase_1_results'].append({
                   'success': True,
                   'action': 'correct_ip',
                   'droplet': droplet_name,
                   'ip': correct_ip
               })
               
               print(f"   ✅ Corrected IP for {droplet_name}: {correct_ip}")
           
           elif action['action'] == 'create_droplet':
               # Create new droplet
               droplet_name = action['droplet']
               config = action['config']
               
               create_result = self._create_droplet(droplet_name, config)
               results['phase_1_results'].append(create_result)
               
               if not create_result['success']:
                   print(f"   ❌ Failed to create {droplet_name}")
                   return False
               else:
                   print(f"   ✅ Created {droplet_name}: {create_result.get('ip')}")
       
       return True
   
   def _execute_phase_2_migrate(self, migrate_actions: List[Dict[str, Any]], results: Dict[str, Any]) -> bool:
       """Execute Phase 2: Migrate services between droplets"""
       
       for action in migrate_actions:
           if action['action'] == 'migrate_services':
               # Migrate services from one droplet to another
               migration_plan = action['migration_plan']
               
               if migration_plan['issues']:
                   print(f"   ❌ Migration issues: {migration_plan['issues']}")
                   return False
               
               # Execute the migration
               self.state.execute_service_migration(migration_plan)
               
               results['phase_2_results'].append({
                   'success': True,
                   'action': 'migrate_services',
                   'from_droplet': action['from_droplet'],
                   'migrations_count': len(migration_plan['migrations'])
               })
               
               print(f"   ✅ Migrated {len(migration_plan['migrations'])} services from {action['from_droplet']}")
           
           elif action['action'] == 'rename_droplet':
               # Update droplet name in JSON (for modified droplets)
               from_name = action['from_name']
               to_name = action['to_name']
               
               # Get the droplet config
               temp_droplet = self.state.get_droplet(from_name)
               if temp_droplet:
                   # Add with new name
                   self.state.add_droplet(
                       name=to_name,
                       ip=temp_droplet['ip'],
                       size=temp_droplet['size'],
                       region=temp_droplet['region'],
                       role=temp_droplet['role'],
                       project=temp_droplet.get('project')
                   )
                   
                   # Remove old name
                   self.state.remove_droplet(from_name)
                   
                   results['phase_2_results'].append({
                       'success': True,
                       'action': 'rename_droplet',
                       'from_name': from_name,
                       'to_name': to_name
                   })
                   
                   print(f"   ✅ Renamed droplet {from_name} → {to_name}")
       
       return True
   
   def _execute_phase_3_cleanup(self, cleanup_actions: List[Dict[str, Any]], results: Dict[str, Any]) -> bool:
       """Execute Phase 3: Destroy old droplets"""
       
       overall_success = True
       
       for action in cleanup_actions:
           if action['action'] == 'destroy_droplet':
               droplet_name = action['droplet']
               
               # Only destroy if it still exists in DO (might have been replaced)
               destroy_result = self._destroy_droplet(droplet_name)
               results['phase_3_results'].append(destroy_result)
               
               if not destroy_result['success']:
                   print(f"   ⚠️  Failed to destroy {droplet_name}: {destroy_result.get('error')}")
                   overall_success = False
               else:
                   print(f"   ✅ Destroyed {droplet_name}")
       
       return overall_success
   
   def _rollback_phase_1(self, rollback_actions: List[Dict[str, Any]], results: Dict[str, Any]):
       """Rollback Phase 1: Destroy any created droplets"""
       
       print("🔄 Rolling back Phase 1...")
       
       for action in rollback_actions:
           if action['phase'] == 1 and action['action'] == 'destroy_droplet':
               droplet_name = action['droplet']
               
               try:
                   destroy_result = self._destroy_droplet(droplet_name)
                   if destroy_result['success']:
                       print(f"   ✅ Rolled back: destroyed {droplet_name}")
                   else:
                       print(f"   ⚠️  Rollback warning: failed to destroy {droplet_name}")
               except Exception as e:
                   print(f"   ⚠️  Rollback error: {e}")
   
   def _rollback_phase_2(self, migrate_actions: List[Dict[str, Any]], results: Dict[str, Any]):
       """Rollback Phase 2: Revert service migrations"""
       
       print("🔄 Rolling back Phase 2...")
       
       # For now, just log that manual intervention may be needed
       # Full service migration rollback would require storing original state
       print("   ⚠️  Service migration rollback requires manual intervention")
       print("   💡 Recommendation: Use snapshot recovery if available")
   
   def _create_droplet(self, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
       """Create a new droplet with enhanced error handling and state management"""
       
       try:
           # Get SSH key ID
           ssh_key_id = self.ssh_manager.get_digitalocean_key_id()
           if not ssh_key_id:
               return {
                   'success': False,
                   'operation': 'create_droplet',
                   'name': name,
                   'error': 'SSH key not found in DigitalOcean'
               }
           
           print(f"      Creating droplet {name} ({config.get('size')}, {config.get('region')})...")
           
           # Create droplet in DigitalOcean
           droplet = self.do_manager.create_droplet(
               name=name,
               size=config['size'],
               region=config['region'],
               ssh_keys=[ssh_key_id]
           )
           
           if not droplet or not droplet.ip_address:
               return {
                   'success': False,
                   'operation': 'create_droplet',
                   'name': name,
                   'error': 'Droplet creation failed or no IP assigned'
               }
           
           # Test SSH connectivity
           print(f"      Testing SSH connectivity to {name} ({droplet.ip_address})...")
           ssh_success = self.ssh_manager.test_connection(droplet.ip_address)
           
           if not ssh_success:
               # Cleanup: destroy the droplet if SSH fails
               try:
                   droplet.destroy()
               except:
                   pass
               
               return {
                   'success': False,
                   'operation': 'create_droplet',
                   'name': name,
                   'error': 'SSH connectivity test failed'
               }
           
           # Update infrastructure state with real IP
           self.state.add_droplet(
               name=name,
               ip=droplet.ip_address,
               size=config['size'],
               region=config['region'],
               role=config['role'],
               project=config.get('project')
           )
           
           # Setup monitoring relationships if needed
           if config.get('role') in ['master', 'web']:
               self._setup_monitoring_relationships(name, config['role'])
           
           return {
               'success': True,
               'operation': 'create_droplet',
               'name': name,
               'ip': droplet.ip_address,
               'do_id': droplet.id
           }
           
       except Exception as e:
           return {
               'success': False,
               'operation': 'create_droplet',
               'name': name,
               'error': str(e)
           }
   
   def _destroy_droplet(self, name: str) -> Dict[str, Any]:
       """Destroy a droplet with enhanced error handling"""
       
       try:
           print(f"      Destroying droplet {name}...")
           
           # Remove from load balancer first if it has web services
           services_on_droplet = self.state.get_services_on_droplet(name)
           web_services = [s for s in services_on_droplet if s['config'].get('type') == 'web']
           
           if web_services:
               print(f"      Removing {name} from load balancer...")
               try:
                   # This will regenerate nginx config without this droplet
                   self.load_balancer_manager.deploy_nginx_config()
               except Exception as e:
                   print(f"      Warning: Load balancer update failed: {e}")
           
           # Get droplet info from state
           droplet_info = self.state.get_droplet(name)
           
           # Destroy in DigitalOcean
           do_success = self.do_manager.destroy_droplet(name)
           
           if do_success:
               # Remove from infrastructure state
               self.state.remove_droplet(name)
               
               return {
                   'success': True,
                   'operation': 'destroy_droplet',
                   'name': name,
                   'ip': droplet_info.get('ip') if droplet_info else None
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
       
       try:
           all_droplets = self.state.get_all_droplets()
           
           if role == 'master':
               # Master monitors all web droplets
               web_droplets = [name for name, config in all_droplets.items() if config['role'] == 'web']
               print(f"      Setup: {droplet_name} will monitor {len(web_droplets)} web droplets")
               
           elif role == 'web':
               # Web droplet will monitor master and peers (handled by health monitor)
               master_droplets = [name for name, config in all_droplets.items() if config['role'] == 'master']
               print(f"      Setup: {droplet_name} will monitor master and peer web droplets")
               
       except Exception as e:
           print(f"      Warning: Failed to setup monitoring relationships: {e}")
   
   # Project Management Methods (Updated for Nested Structure)
   def add_project(self, project: str, environments: List[str], 
                  web_droplets: int, web_droplet_spec: str) -> Dict[str, Any]:
       """Add a new project to infrastructure"""
       
       try:
           print(f"➕ Adding project: {project}")
           
           # Add to state using new nested structure
           self.state.add_project_spec(project, environments, web_droplets, web_droplet_spec)
           
           # Re-orchestrate
           result = self.orchestrate_infrastructure()
           
           return {
               'success': result['success'],
               'project': project,
               'environments': environments,
               'web_droplets': web_droplets,
               'orchestration_result': result
           }
           
       except Exception as e:
           return {
               'success': False,
               'error': str(e)
           }

   def scale_project(self, project: str, target_web_droplets: int) -> Dict[str, Any]:
       """Scale a project to target number of web droplets"""
       
       print(f"📈 Scaling {project} to {target_web_droplets} web droplets")
       
       try:
           # Find existing project droplets to update
           current_droplets = self.state.get_droplets_by_project(project)
           current_web_count = len([d for d in current_droplets.values() if d.get('role') == 'web'])
           
           if target_web_droplets > current_web_count:
               # Add new droplets
               for i in range(current_web_count + 1, target_web_droplets + 1):
                   droplet_name = f"{project}-web{i}"
                   
                   # Get the web spec from existing droplets
                   existing_web = next((d for d in current_droplets.values() if d.get('role') == 'web'), None)
                   web_spec = existing_web['size'] if existing_web else 's-1vcpu-1gb'
                   
                   self.state.add_droplet(
                       name=droplet_name,
                       ip=None,  # Will be set when created
                       size=web_spec,
                       region="lon1",
                       role="web",
                       project=project
                   )
           
           elif target_web_droplets < current_web_count:
               # Remove excess droplets
               web_droplets_to_remove = []
               for name, config in current_droplets.items():
                   if config.get('role') == 'web':
                       droplet_num = int(name.split('web')[-1])
                       if droplet_num > target_web_droplets:
                           web_droplets_to_remove.append(name)
               
               for droplet_name in web_droplets_to_remove:
                   self.state.remove_droplet(droplet_name)
           
           # Re-orchestrate infrastructure
           result = self.orchestrate_infrastructure()
           
           return {
               'success': result['success'],
               'project': project,
               'target_web_droplets': target_web_droplets,
               'orchestration_result': result
           }
           
       except Exception as e:
           return {
               'success': False,
               'error': str(e)
           }

   def remove_project(self, project: str) -> Dict[str, Any]:
       """Remove a project from infrastructure"""
       
       try:
           print(f"➖ Removing project: {project}")
           
           # Remove from state
           self.state.remove_project_spec(project)
           
           # Re-orchestrate
           result = self.orchestrate_infrastructure()
           
           return {
               'success': result['success'],
               'project': project,
               'orchestration_result': result
           }
           
       except Exception as e:
           return {
               'success': False,
               'error': str(e)
           }
   
   # Deployment operations
   def deploy_to_uat(self, project: str, branch: str = "main", use_local: bool = False, 
                     local_project_path: str = None) -> Dict[str, Any]:
       """Deploy project to UAT environment"""
       
       if not self.deployment_manager:
           return {'success': False, 'error': 'Deployment manager not initialized'}
       
       print(f"🚀 Deploying {project} to UAT from {'local' if use_local else 'git'}")
       
       try:
           result = self.deployment_manager.deploy_to_uat(
               project, branch, use_local, local_project_path
           )
           
           if result.get('status') == 'success':
               # Update load balancer after deployment
               lb_result = self.load_balancer_manager.deploy_nginx_config()
               result['load_balancer_update'] = lb_result
           
           return result
           
       except Exception as e:
           return {
               'success': False,
               'error': str(e)
           }
   
   def deploy_to_prod(self, project: str, use_uat_tag: bool = True, 
                      promote_images: bool = True) -> Dict[str, Any]:
       """Deploy project to production environment"""
       
       if not self.deployment_manager:
           return {'success': False, 'error': 'Deployment manager not initialized'}
       
       print(f"🚀 Deploying {project} to production")
       
       try:
           result = self.deployment_manager.deploy_to_prod(
               project, use_uat_tag, promote_images=promote_images
           )
           
           if result.get('status') == 'success':
               # Update load balancer after deployment
               lb_result = self.load_balancer_manager.deploy_nginx_config()
               result['load_balancer_update'] = lb_result
           
           return result
           
       except Exception as e:
           return {
               'success': False,
               'error': str(e)
           }
   
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
   parser.add_argument('--orchestrate', action='store_true', help='Orchestrate infrastructure from JSON spec')
   parser.add_argument('--status', action='store_true', help='Get infrastructure status')
   
   # Project management
   parser.add_argument('--add-project', nargs=4, metavar=('PROJECT', 'ENVIRONMENTS', 'WEB_DROPLETS', 'WEB_SPEC'), 
                      help='Add project (e.g., --add-project myapp "prod,uat" 2 s-2vcpu-4gb)')
   parser.add_argument('--remove-project', metavar='PROJECT', help='Remove project')
   parser.add_argument('--scale', nargs=2, metavar=('PROJECT', 'WEB_DROPLETS'), help='Scale project web droplets')
   
   # Deployment
   parser.add_argument('--deploy-uat', metavar='PROJECT', help='Deploy project to UAT')
   parser.add_argument('--deploy-prod', metavar='PROJECT', help='Deploy project to production')
   
   # Operations
   parser.add_argument('--monitor', metavar='DROPLET', help='Start health monitoring on droplet')
   parser.add_argument('--recover', metavar='DROPLET', help='Emergency recovery of failed droplet')
   parser.add_argument('--cleanup', action='store_true', help='Clean up old resources')
   parser.add_argument('--update-ip', metavar='NEW_IP', help='Update administrator IP')
   parser.add_argument('--reproduce', metavar='TAG', help='Reproduce deployment from tag')
   
   # Orchestration flags
   parser.add_argument('--force', action='store_true', help='Force recreate resources')
   parser.add_argument('--yes', action='store_true', help='Skip confirmation prompts')
   parser.add_argument('--blue-green', action='store_true', help='Use blue/green deployment strategy')
   parser.add_argument('--dry-run', action='store_true', help='Show what would be done without executing')
   
   # Other flags
   parser.add_argument('--local', action='store_true', help='Use local codebase instead of git clone')
   parser.add_argument('--project-path', metavar='PATH', help='Path to local project directory')
   parser.add_argument('--reproduce-dir', metavar='DIR', help='Directory for reproduced code')
   parser.add_argument('--rebuild-images', action='store_true', help='Rebuild images from source instead of promoting UAT images')
   
   args = parser.parse_args()
   
   # Initialize orchestrator
   orchestrator = InfrastructureOrchestrator()
   
   if args.init:
       result = orchestrator.initialize_system()
       print(json.dumps(result, indent=2))
   
   elif args.orchestrate:
       result = orchestrator.orchestrate_infrastructure(
           force_recreate=args.force,
           skip_confirmation=args.yes
       )
       print(json.dumps(result, indent=2))
   
   elif args.status:
       result = orchestrator.get_infrastructure_status()
       print(json.dumps(result, indent=2))
   
   elif args.add_project:
       project, envs_str, web_count, web_spec = args.add_project
       environments = [env.strip() for env in envs_str.split(',')]
       result = orchestrator.add_project(project, environments, int(web_count), web_spec)
       print(json.dumps(result, indent=2))
   
   elif args.remove_project:
       result = orchestrator.remove_project(args.remove_project)
       print(json.dumps(result, indent=2))
   
   elif args.scale:
       project, web_droplets = args.scale
       result = orchestrator.scale_project(project, int(web_droplets))
       print(json.dumps(result, indent=2))
   
   elif args.deploy_uat:
       result = orchestrator.deploy_to_uat(
           args.deploy_uat, 
           use_local=args.local,
           local_project_path=args.project_path
       )
       print(json.dumps(result, indent=2))
   
   elif args.deploy_prod:
       result = orchestrator.deploy_to_prod(
           args.deploy_prod, 
           promote_images=not args.rebuild_images
       )
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
       if orchestrator.deployment_manager:
           result = orchestrator.deployment_manager.reproduce_deployment(
               args.reproduce, 
               args.reproduce_dir
           )
           print(json.dumps(result, indent=2))
       else:
           print(json.dumps({'error': 'Deployment manager not initialized'}, indent=2))
   
   else:
       parser.print_help()

if __name__ == '__main__':
   main()