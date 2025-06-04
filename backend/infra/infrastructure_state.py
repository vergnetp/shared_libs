"""
Infrastructure State Management

Manages the normalized JSON state for the entire infrastructure,
including droplets, projects, services, and computed relationships.
Uses JSON as the single source of truth with transactional orchestration support.
"""

import json
import hashlib
from typing import Dict, List, Any, Optional
from pathlib import Path


class InfrastructureState:
   """
   Manages the normalized infrastructure state with computed relationships.
   Single source of truth for all infrastructure configuration.
   """
   
   def __init__(self, state_file: str = "config/infrastructure.json"):
       self.state_file = Path(state_file)
       self.state = self._load_state()
       
   def _load_state(self) -> Dict[str, Any]:
        """Load state from JSON file or create empty state"""
        if self.state_file.exists():
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                # Apply defaults after loading
                self._apply_defaults(state)
                return state
        else:
            return self._create_empty_state()
    
   def _create_empty_state(self) -> Dict[str, Any]:
        """Create empty state structure with new format"""
        state = {
            "health_monitoring": {
                "heartbeat_config": {
                    "interval_minutes": 15,
                    "check_interval_seconds": 60,
                    "failure_timeout_minutes": 10,
                    "health_timeout_seconds": 20
                }
            },
            "droplets": {},
            "projects": {}
        }
        return state
   
   def save_state(self):
        """Save current state to JSON file with defaults applied"""
        self._apply_defaults(self.state)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
   
   def get_environment_heartbeat_config(self, project: str, environment: str) -> Dict[str, Any]:
        """Get heartbeat config with inheritance: global -> project -> environment"""
        
        # Start with global default
        config = self.state.get("health_monitoring", {}).get("heartbeat_config", {
            "interval_minutes": 15,
            "check_interval_seconds": 60,
            "failure_timeout_minutes": 10,
            "health_timeout_seconds": 20
        })
        
        # Apply project-level overrides
        project_config = self.state.get("projects", {}).get(project, {})
        project_health = project_config.get("health_monitoring", {}).get("heartbeat_config", {})
        config.update(project_health)
        
        # Apply environment-level overrides
        env_config = project_config.get(environment, {})
        env_health = env_config.get("health_monitoring", {}).get("heartbeat_config", {})
        config.update(env_health)
        
        return config

   def update_environment_heartbeat_config(self, project: str, environment: str, **config_updates):
        """Update heartbeat monitoring configuration for a specific environment"""
        # Ensure environment exists
        if project not in self.state["projects"]:
            raise ValueError(f"Project {project} not found")
        if environment not in self.state["projects"][project]:
            raise ValueError(f"Environment {environment} not found in project {project}")
        
        # Ensure health_monitoring structure exists
        if "health_monitoring" not in self.state["projects"][project][environment]:
            self.state["projects"][project][environment]["health_monitoring"] = {}
        if "heartbeat_config" not in self.state["projects"][project][environment]["health_monitoring"]:
            self.state["projects"][project][environment]["health_monitoring"]["heartbeat_config"] = {}
        
        # Update configuration
        self.state["projects"][project][environment]["health_monitoring"]["heartbeat_config"].update(config_updates)
        self.save_state()

   # Transactional Orchestration Methods
   def get_desired_droplets(self) -> Dict[str, Dict[str, Any]]:
       """Get droplets as defined in JSON (desired state)"""
       return self.state.get("droplets", {})
   
   def get_actual_droplets_from_do(self, do_manager) -> Dict[str, Dict[str, Any]]:
       """Get actual droplets from DigitalOcean"""
       actual_droplets = {}
       
       try:
           do_droplets = do_manager.manager.get_all_droplets()
           for droplet in do_droplets:
               actual_droplets[droplet.name] = {
                   "ip": droplet.ip_address,
                   "size": droplet.size_slug,
                   "region": droplet.region['slug'],
                   "status": droplet.status,
                   "id": droplet.id
               }
       except Exception as e:
           print(f"Error fetching droplets from DigitalOcean: {e}")
       
       return actual_droplets
   
   def get_droplets_to_create(self, do_manager) -> List[Dict[str, Any]]:
       """Get droplets that need to be created"""
       desired = self.get_desired_droplets()
       actual = self.get_actual_droplets_from_do(do_manager)
       
       to_create = []
       
       for name, config in desired.items():
           if name not in actual:
               # Droplet doesn't exist in DO
               to_create.append({
                   'name': name,
                   'config': config,
                   'reason': 'missing_in_do'
               })
           elif config.get('ip') is None:
               # IP is null, user wants recreation
               to_create.append({
                   'name': name,
                   'config': config,
                   'reason': 'recreation_requested'
               })
       
       return to_create
   
   def get_droplets_to_modify(self, do_manager) -> List[Dict[str, Any]]:
       """Get droplets that need to be modified (recreated with different specs)"""
       desired = self.get_desired_droplets()
       actual = self.get_actual_droplets_from_do(do_manager)
       
       to_modify = []
       
       for name, desired_config in desired.items():
           if name in actual and desired_config.get('ip') is not None:
               actual_config = actual[name]
               
               # Check if size or region changed
               size_changed = desired_config.get('size') != actual_config.get('size')
               region_changed = desired_config.get('region') != actual_config.get('region')
               
               if size_changed or region_changed:
                   changes = []
                   if size_changed:
                       changes.append(f"size: {actual_config.get('size')} → {desired_config.get('size')}")
                   if region_changed:
                       changes.append(f"region: {actual_config.get('region')} → {desired_config.get('region')}")
                   
                   to_modify.append({
                       'name': name,
                       'current_config': actual_config,
                       'desired_config': desired_config,
                       'changes': changes,
                       'reason': 'specs_changed'
                   })
       
       return to_modify
   
   def get_droplets_to_delete(self, do_manager) -> List[Dict[str, Any]]:
       """Get droplets that exist in DO but not in desired state"""
       desired = self.get_desired_droplets()
       actual = self.get_actual_droplets_from_do(do_manager)
       
       to_delete = []
       
       for name, actual_config in actual.items():
           if name not in desired:
               to_delete.append({
                   'name': name,
                   'config': actual_config,
                   'reason': 'not_in_desired_state'
               })
       
       return to_delete
   
   def get_ip_corrections_needed(self, do_manager) -> List[Dict[str, Any]]:
       """Get droplets where JSON IP doesn't match DO IP (need correction)"""
       desired = self.get_desired_droplets()
       actual = self.get_actual_droplets_from_do(do_manager)
       
       corrections = []
       
       for name, desired_config in desired.items():
           if name in actual and desired_config.get('ip') is not None:
               desired_ip = desired_config.get('ip')
               actual_ip = actual[name].get('ip')
               
               if desired_ip != actual_ip:
                   corrections.append({
                       'name': name,
                       'desired_ip': desired_ip,
                       'actual_ip': actual_ip,
                       'action': 'update_json_with_actual_ip'
                   })
       
       return corrections
   
   def update_droplet_ip(self, name: str, new_ip: str):
       """Update droplet IP address in JSON"""
       if name in self.state["droplets"]:
           self.state["droplets"][name]["ip"] = new_ip
           self.save_state()
   
   def get_services_on_droplet(self, droplet_name: str) -> List[Dict[str, Any]]:
        """Get all services running on a specific droplet with new structure"""
        services = []
        
        for project, environments in self.state.get("projects", {}).items():
            for environment, env_config in environments.items():
                if environment == "health_monitoring":
                    continue
                
                project_services = env_config.get("services", {})
                
                for service_name, service_config in project_services.items():
                    assigned_droplets = service_config.get("assigned_droplets", [])
                    if droplet_name in assigned_droplets:
                        services.append({
                            'project': project,
                            'environment': environment,
                            'service_name': service_name,
                            'service_type': service_config.get("type"),
                            'config': service_config
                        })
                
                # Handle workers array
                workers = env_config.get("services", {}).get("workers", [])
                if isinstance(workers, list):
                    for i, worker_config in enumerate(workers):
                        assigned_droplets = worker_config.get("assigned_droplets", [])
                        if droplet_name in assigned_droplets:
                            services.append({
                                'project': project,
                                'environment': environment,
                                'service_name': f"worker_{i}",
                                'service_type': "worker",
                                'config': worker_config
                            })
        
        return services
   
   def get_candidate_droplets_for_service_migration(self, service_info: Dict[str, Any], 
                                                   exclude_droplets: List[str] = None) -> List[str]:
       """Get candidate droplets where a service can be migrated"""
       exclude_droplets = exclude_droplets or []
       service_type = service_info['config'].get('type', 'web')
       
       candidates = []
       
       for droplet_name, droplet_config in self.state["droplets"].items():
           if droplet_name in exclude_droplets:
               continue
           
           droplet_role = droplet_config.get('role')
           
           # Service type placement rules
           if service_type == 'worker':
               # Workers can go anywhere
               candidates.append(droplet_name)
           elif service_type == 'web':
               # Web services prefer web droplets, but can go on master if needed
               if droplet_role in ['web', 'master']:
                   candidates.append(droplet_name)
           elif service_type == 'infrastructure':
               # Infrastructure services prefer master
               if droplet_role == 'master':
                   candidates.append(droplet_name)
       
       return candidates
   
   def plan_service_migration(self, droplet_to_remove: str) -> Dict[str, Any]:
       """Plan how to migrate services away from a droplet that will be removed"""
       services_to_migrate = self.get_services_on_droplet(droplet_to_remove)
       migration_plan = {
           'services_to_migrate': services_to_migrate,
           'migrations': [],
           'issues': []
       }
       
       for service_info in services_to_migrate:
           candidates = self.get_candidate_droplets_for_service_migration(
               service_info, 
               exclude_droplets=[droplet_to_remove]
           )
           
           if not candidates:
               migration_plan['issues'].append(
                   f"No suitable droplets found for {service_info['service_name']}"
               )
           else:
               # Simple strategy: pick first available candidate
               # TODO: More sophisticated load balancing
               target_droplet = candidates[0]
               
               migration_plan['migrations'].append({
                   'service': service_info,
                   'from_droplet': droplet_to_remove,
                   'to_droplet': target_droplet
               })
       
       return migration_plan
   
   def execute_service_migration(self, migration_plan: Dict[str, Any]):
       """Execute the planned service migrations"""
       for migration in migration_plan['migrations']:
           service_info = migration['service']
           from_droplet = migration['from_droplet']
           to_droplet = migration['to_droplet']
           
           # Update service assignment
           project = service_info['project']
           environment = service_info['environment']
           service_type = service_info['service_type']
           
           current_droplets = service_info['config']['assigned_droplets']
           updated_droplets = [
               to_droplet if d == from_droplet else d 
               for d in current_droplets
           ]
           
           # Update the service configuration
           self.state["projects"][project][environment][service_type]["assigned_droplets"] = updated_droplets
       
       self.save_state()
   
   # Project and Service Management (Updated for Nested Structure)
   def _get_flat_project_key(self, project: str, environment: str) -> str:
       """Generate flat project key from project and environment"""
       return f"{project}-{environment}"
   
   def get_project_services(self, project: str, environment: str) -> Dict[str, Any]:
        """Get all services for a project/environment in new structure"""
        
        if project not in self.state.get("projects", {}):
            return {}
        
        env_config = self.state["projects"][project].get(environment, {})
        return env_config.get("services", {})
    
   def get_all_projects(self) -> Dict[str, Dict[str, Any]]:
        """Get all projects with flattened structure for backward compatibility"""
        
        flat_projects = {}
        
        for project, environments in self.state.get("projects", {}).items():
            for environment, env_config in environments.items():
                if environment == "health_monitoring":
                    continue
                
                flat_key = f"{project}-{environment}"
                
                # Convert new structure to old flat structure
                services = env_config.get("services", {})
                flat_services = {}
                
                for service_name, service_config in services.items():
                    if service_name == "workers":
                        # Handle workers array - convert to individual services
                        if isinstance(service_config, list):
                            for i, worker in enumerate(service_config):
                                flat_services[f"worker_{i}"] = worker
                    else:
                        flat_services[service_name] = service_config
                
                flat_projects[flat_key] = flat_services
        
        return flat_projects
   
   def add_project_service(self, project: str, service_type: str, environment: str = None,
                          port: int = None, assigned_droplets: List[str] = None, 
                          service_config: Dict[str, Any] = None):
       """Add a service to a project"""
       
       # Parse project-environment if passed as single string
       if environment is None and '-' in project:
           project, environment = project.rsplit('-', 1)
       
       # Ensure nested structure exists
       if project not in self.state["projects"]:
           self.state["projects"][project] = {}
       if environment not in self.state["projects"][project]:
           self.state["projects"][project][environment] = {}
       
       # Build service data
       service_data = {
           "assigned_droplets": assigned_droplets or []
       }
       
       if port is not None:
           service_data["port"] = port
           
       # Add type for workers (infer from service_type if not provided)
       if service_config and "type" in service_config:
           service_data["type"] = service_config["type"]
       elif service_type.startswith("worker_"):
           service_data["type"] = "worker"
       else:
           service_data["type"] = "web"
           
       if service_config:
           service_data.update(service_config)
       
       self.state["projects"][project][environment][service_type] = service_data
       self.save_state()
   
   def remove_project_service(self, project: str, service_type: str, environment: str = None):
       """Remove a service from a project"""
       
       # Parse project-environment if passed as single string
       if environment is None and '-' in project:
           project, environment = project.rsplit('-', 1)
       
       if (project in self.state["projects"] and 
           environment in self.state["projects"][project] and 
           service_type in self.state["projects"][project][environment]):
           
           del self.state["projects"][project][environment][service_type]
           
           # Clean up empty environment
           if not self.state["projects"][project][environment]:
               del self.state["projects"][project][environment]
               
           # Clean up empty project
           if not self.state["projects"][project]:
               del self.state["projects"][project]
               
           self.save_state()
   
   # Droplet Management
   def add_droplet(self, name: str, ip: str, size: str, region: str, role: str, monitors: List[str] = None, project: str = None):
       """Add a new droplet to the state"""
       droplet_data = {
           "ip": ip,
           "size": size,
           "region": region,
           "role": role
       }
       
       if project:
           droplet_data["project"] = project
           
       self.state["droplets"][name] = droplet_data
       self.save_state()
   
   def remove_droplet(self, name: str):
       """Remove droplet from state"""
       if name in self.state["droplets"]:
           del self.state["droplets"][name]
           self.save_state()
   
   def get_droplet(self, name: str) -> Optional[Dict[str, Any]]:
       """Get droplet configuration"""
       return self.state["droplets"].get(name)
   
   def get_all_droplets(self) -> Dict[str, Dict[str, Any]]:
       """Get all droplets"""
       return self.state["droplets"]
   
   def get_droplets_by_role(self, role: str) -> Dict[str, Dict[str, Any]]:
       """Get droplets filtered by role"""
       return {
           name: droplet for name, droplet in self.state["droplets"].items()
           if droplet.get("role") == role
       }
   
   def get_droplets_by_project(self, project: str) -> Dict[str, Dict[str, Any]]:
       """Get droplets filtered by project"""
       return {
           name: droplet for name, droplet in self.state["droplets"].items()
           if droplet.get("project") == project
       }
   
   # Legacy Methods (Updated for Compatibility)
   def get_required_droplets(self) -> Dict[str, Dict[str, Any]]:
       """Get required droplets (same as desired droplets)"""
       return self.get_desired_droplets()
   
   def get_required_services(self) -> Dict[str, Dict[str, Any]]:
       """Get required services (same as current services with flat keys)"""
       return self.get_all_projects()
   
   def add_project_spec(self, project: str, environments: List[str], 
                       web_droplets: int, web_droplet_spec: str):
       """Add project by creating actual droplets and project structure"""
       
       # Create web droplets for this project
       for i in range(1, web_droplets + 1):
           droplet_name = f"{project}-web{i}"
           # Note: IP will be set when droplet is actually created
           self.state["droplets"][droplet_name] = {
               "ip": None,  # Will be filled when created
               "size": web_droplet_spec,
               "region": "lon1",  # Default region
               "role": "web",
               "project": project
           }
       
       # Create project structure for each environment
       if project not in self.state["projects"]:
           self.state["projects"][project] = {}
           
       for environment in environments:
           if environment not in self.state["projects"][project]:
               self.state["projects"][project][environment] = {}
               
               # Add default services
               web_droplet_names = [f"{project}-web{i}" for i in range(1, web_droplets + 1)]
               assigned_droplets = web_droplet_names if web_droplets > 0 else ["master"]
               
               # Default service configuration
               self.state["projects"][project][environment] = {
                   "backend": {
                       "type": "web",
                       "port": self.get_hash_based_port(project, environment, 8000, 1000),
                       "assigned_droplets": assigned_droplets[:2] if len(assigned_droplets) > 1 else assigned_droplets
                   },
                   "frontend": {
                       "type": "web",
                       "port": self.get_hash_based_port(project, environment, 9000, 1000),
                       "assigned_droplets": assigned_droplets[:2] if len(assigned_droplets) > 1 else assigned_droplets
                   }
               }
               
               # Add worker if we have dedicated droplets
               if web_droplets > 0:
                   self.state["projects"][project][environment]["worker_cleaner"] = {
                       "type": "worker",
                       "assigned_droplets": [assigned_droplets[0]]
                   }
       
       self.save_state()
   
   def remove_project_spec(self, project: str):
       """Remove project and its associated droplets"""
       
       # Remove project droplets
       droplets_to_remove = []
       for droplet_name, droplet_config in self.state["droplets"].items():
           if droplet_config.get("project") == project:
               droplets_to_remove.append(droplet_name)
       
       for droplet_name in droplets_to_remove:
           del self.state["droplets"][droplet_name]
       
       # Remove project
       if project in self.state["projects"]:
           del self.state["projects"][project]
       
       self.save_state()
   
   # Computed Relationships
   def get_service_name(self, project: str, service_type: str) -> str:
       """Generate service name from project and service type"""
       return f"{project}-{service_type}"
   
   def get_load_balancer_targets(self, project: str, service_type: str) -> List[str]:
        """Get load balancer targets for a service (web services only)"""
        
        # Extract environment from flattened project key
        if '-' in project:
            project_name, environment = project.rsplit('-', 1)
        else:
            return []
        
        services = self.get_project_services(project_name, environment)
        service_config = services.get(service_type, {})
        
        # Skip non-web services
        if service_config.get("type") != "web":
            return []
        
        targets = []
        assigned_droplets = service_config.get("assigned_droplets", [])
        port = service_config.get("port")
        
        if not port:
            return []
        
        for droplet_name in assigned_droplets:
            droplet = self.get_droplet(droplet_name)
            if droplet and droplet.get('ip'):
                targets.append(f"{droplet['ip']}:{port}")
        
        return targets
   
   def get_monitored_by(self, droplet_name: str) -> List[str]:
       """Get list of droplets that monitor the given droplet"""
       monitors = []
       for name, droplet in self.state["droplets"].items():
           if droplet_name in droplet.get("monitors", []):
               monitors.append(name)
       return monitors
   
   def generate_resource_hash(self, project: str, environment: str) -> str:
       """Generate deterministic hash for resource naming"""
       hash_input = f"{project}-{environment}".encode()
       return hashlib.md5(hash_input).hexdigest()[:12]  # 12 char hash
   
   def get_hash_based_port(self, project: str, environment: str, base_port: int, port_range: int = 1000) -> int:
       """Generate hash-based port allocation"""
       resource_hash = self.generate_resource_hash(project, environment)
       return base_port + (int(resource_hash, 16) % port_range)
   
   # Health Monitoring
   def update_heartbeat_config(self, interval_minutes: int = None):
       """Update heartbeat monitoring configuration"""
       if interval_minutes:
           self.state["health_monitoring"]["heartbeat_config"]["interval_minutes"] = interval_minutes
       self.save_state()
   
   def get_heartbeat_config(self) -> Dict[str, Any]:
       """Get heartbeat monitoring configuration"""
       return self.state["health_monitoring"]["heartbeat_config"]
   
   # Utility Methods
   def get_master_droplet(self) -> Optional[Dict[str, Any]]:
       """Get the master droplet"""
       master_droplets = self.get_droplets_by_role("master")
       if master_droplets:
           return list(master_droplets.values())[0]
       return None
   
   def get_web_droplets(self) -> Dict[str, Dict[str, Any]]:
       """Get all web droplets"""
       return self.get_droplets_by_role("web")
   
   def validate_state(self) -> List[str]:
       """Validate the current state and return any issues"""
       issues = []
       
       # Check for missing master droplet
       if not self.get_master_droplet():
           issues.append("No master droplet found")
       
       # Check for services assigned to non-existent droplets
       for project, environments in self.state["projects"].items():
           for environment, services in environments.items():
               for service_type, service_config in services.items():
                   for droplet_name in service_config.get("assigned_droplets", []):
                       if droplet_name not in self.state["droplets"]:
                           issues.append(f"Service {project}-{environment}-{service_type} assigned to non-existent droplet {droplet_name}")
       
       # Check for duplicate ports on same droplet
       droplet_ports = {}
       for project, environments in self.state["projects"].items():
           for environment, services in environments.items():
               for service_type, service_config in services.items():
                   if "port" in service_config:
                       port = service_config["port"]
                       for droplet_name in service_config.get("assigned_droplets", []):
                           if droplet_name not in droplet_ports:
                               droplet_ports[droplet_name] = []
                           if port in droplet_ports[droplet_name]:
                               issues.append(f"Port {port} conflict on droplet {droplet_name}")
                           else:
                               droplet_ports[droplet_name].append(port)
       
       return issues
   
   def get_summary(self) -> Dict[str, Any]:
       """Get infrastructure summary"""
       droplet_count = len(self.state["droplets"])
       project_count = len(self.state["projects"])
       
       service_count = 0
       worker_count = 0
       web_service_count = 0
       infrastructure_service_count = 0
       
       for project, environments in self.state["projects"].items():
           for environment, services in environments.items():
               for service_type, service_config in services.items():
                   service_count += 1
                   service_type_info = service_config.get("type", "web")
                   
                   if service_type_info == "worker":
                       worker_count += 1
                   elif service_type_info == "web":
                       web_service_count += 1
                   elif service_type_info == "infrastructure":
                       infrastructure_service_count += 1
       
       return {
           "droplets": droplet_count,
           "projects": project_count,
           "services": service_count,
           "worker_services": worker_count,
           "web_services": web_service_count,
           "infrastructure_services": infrastructure_service_count,
           "master_ip": self.get_master_droplet()["ip"] if self.get_master_droplet() else None,
           "web_droplet_count": len(self.get_web_droplets()),
           "validation_issues": self.validate_state()
       }
   
   def get_master_for_project(self, project: str, environment: str) -> Optional[Dict[str, Any]]:
        """Get master droplet assigned to project/environment"""
        
        if project not in self.state.get("projects", {}):
            return self.get_master_droplet()
        
        env_config = self.state["projects"][project].get(environment, {})
        services = env_config.get("services", {})
        master_service = services.get("master", {})
        
        if master_service.get("type") == "master":
            assigned_droplets = master_service.get("assigned_droplets", [])
            if assigned_droplets:
                master_name = assigned_droplets[0]
                return self.get_droplet(master_name)
        
        # Fallback to global master
        return self.get_master_droplet()
   
   def _apply_defaults(self, state: Dict[str, Any]):
        """Apply defaults to droplets (size, region) and auto-generate ports"""
        
        # Apply droplet defaults
        for name, droplet_config in state.get("droplets", {}).items():
            # Apply defaults
            if "size" not in droplet_config:
                droplet_config["size"] = "s-1vcpu-1gb"
            if "region" not in droplet_config:
                droplet_config["region"] = "lon1"
            
            # Validate required fields
            if "role" not in droplet_config:
                raise ValueError(f"Droplet {name} missing required 'role' field")

        # Auto-generate ports for web services
        for project, environments in state.get("projects", {}).items():
            for environment, env_config in environments.items():
                if environment == "health_monitoring":
                    continue
                
                services = env_config.get("services", {})
                for service_name, service_config in services.items():
                    if service_config.get("type") == "web" and "port" not in service_config:
                        # Generate hash-based port
                        service_config["port"] = self.get_hash_based_port(
                            project, environment, 8000, 1000
                        )

   def get_workers_for_project(self, project: str, environment: str) -> List[Dict[str, Any]]:
        """Get all workers for a project/environment"""
        
        if project not in self.state.get("projects", {}):
            return []
        
        env_config = self.state["projects"][project].get(environment, {})
        services = env_config.get("services", {})
        workers = services.get("workers", [])
        
        if isinstance(workers, list):
            return workers
        
        return []
    
   def add_worker_to_project(self, project: str, environment: str, worker_config: Dict[str, Any]):
        """Add a worker to the workers array for a project/environment"""
        
        # Ensure project structure exists
        if project not in self.state.setdefault("projects", {}):
            self.state["projects"][project] = {}
        
        if environment not in self.state["projects"][project]:
            self.state["projects"][project][environment] = {"services": {}}
        
        if "services" not in self.state["projects"][project][environment]:
            self.state["projects"][project][environment]["services"] = {}
        
        services = self.state["projects"][project][environment]["services"]
        
        # Initialize workers array if it doesn't exist
        if "workers" not in services:
            services["workers"] = []
        
        # Ensure worker has required type
        worker_config["type"] = "worker"
        
        # Add worker to array
        services["workers"].append(worker_config)
        
        self.save_state()
    
   def remove_worker_from_project(self, project: str, environment: str, worker_index: int):
        """Remove a worker by index from the workers array"""
        
        if project not in self.state.get("projects", {}):
            return False
        
        env_config = self.state["projects"][project].get(environment, {})
        services = env_config.get("services", {})
        workers = services.get("workers", [])
        
        if isinstance(workers, list) and 0 <= worker_index < len(workers):
            workers.pop(worker_index)
            self.save_state()
            return True
        
        return False