"""
Infrastructure State Management

Manages the normalized JSON state for the entire infrastructure,
including droplets, projects, services, and computed relationships.
Now uses JSON as the single source of truth instead of CSV.
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
                return json.load(f)
        else:
            return self._create_empty_state()
    
    def _create_empty_state(self) -> Dict[str, Any]:
        """Create empty state structure with droplet specs"""
        return {
            "droplets": {},
            "projects": {},
            "health_monitoring": {
                "heartbeat_config": {
                    "primary_sender": "master",
                    "backup_senders": [],
                    "interval_minutes": 15
                }
            },
            "infrastructure_spec": {
                "droplets": {
                    "master": {
                        "size": "s-2vcpu-4gb",
                        "region": "lon1",
                        "role": "master"
                    }
                },
                "projects": {
                    "hostomatic": {
                        "environments": ["prod", "uat"],
                        "web_droplets": 2,
                        "web_droplet_spec": "s-2vcpu-4gb"
                    },
                    "digitalpixo": {
                        "environments": ["prod", "uat"],
                        "web_droplets": 1,
                        "web_droplet_spec": "s-1vcpu-1gb"
                    }
                }
            }
        }
    
    def save_state(self):
        """Save current state to JSON file"""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    # Infrastructure Specification Management
    def get_infrastructure_spec(self) -> Dict[str, Any]:
        """Get infrastructure specification"""
        return self.state.get("infrastructure_spec", {})

    def update_infrastructure_spec(self, spec: Dict[str, Any]):
        """Update infrastructure specification"""
        self.state["infrastructure_spec"] = spec
        self.save_state()

    def add_project_spec(self, project: str, environments: List[str], 
                        web_droplets: int, web_droplet_spec: str):
        """Add project specification"""
        if "infrastructure_spec" not in self.state:
            self.state["infrastructure_spec"] = {"droplets": {}, "projects": {}}
        
        if "projects" not in self.state["infrastructure_spec"]:
            self.state["infrastructure_spec"]["projects"] = {}
        
        self.state["infrastructure_spec"]["projects"][project] = {
            "environments": environments,
            "web_droplets": web_droplets,
            "web_droplet_spec": web_droplet_spec
        }
        self.save_state()

    def remove_project_spec(self, project: str):
        """Remove project specification"""
        if "infrastructure_spec" in self.state and "projects" in self.state["infrastructure_spec"]:
            self.state["infrastructure_spec"]["projects"].pop(project, None)
            self.save_state()

    def get_required_droplets(self) -> Dict[str, Dict[str, Any]]:
        """Calculate required droplets from spec"""
        spec = self.get_infrastructure_spec()
        required_droplets = {}
        
        # Add droplets from spec
        droplet_specs = spec.get("droplets", {})
        for name, config in droplet_specs.items():
            required_droplets[name] = config.copy()
        
        # Add web droplets for each project
        project_specs = spec.get("projects", {})
        for project, project_config in project_specs.items():
            web_count = project_config.get("web_droplets", 0)
            web_spec = project_config.get("web_droplet_spec", "s-1vcpu-1gb")
            
            for i in range(1, web_count + 1):
                droplet_name = f"{project}-web{i}"
                required_droplets[droplet_name] = {
                    "size": web_spec,
                    "region": "lon1",
                    "role": "web",
                    "project": project
                }
        
        return required_droplets

    def get_required_services(self) -> Dict[str, Dict[str, Any]]:
        """Calculate required services from spec"""
        spec = self.get_infrastructure_spec()
        required_services = {}
        
        project_specs = spec.get("projects", {})
        for project, project_config in project_specs.items():
            environments = project_config.get("environments", [])
            web_count = project_config.get("web_droplets", 0)
            
            for env in environments:
                project_key = f"{project}-{env}"
                
                # Determine droplet assignments
                if web_count == 0:
                    # Everything on master
                    assigned_droplets = ["master"]
                else:
                    # Distribute across project web droplets
                    web_droplets = [f"{project}-web{i}" for i in range(1, web_count + 1)]
                    assigned_droplets = web_droplets
                
                # Standard services for each project-environment
                services = {
                    "backend": {
                        "type": "web",
                        "port": self.get_hash_based_port(project, env, 8000, 1000),
                        "assigned_droplets": assigned_droplets[:2] if len(assigned_droplets) > 1 else assigned_droplets
                    },
                    "frontend": {
                        "type": "web", 
                        "port": self.get_hash_based_port(project, env, 9000, 1000),
                        "assigned_droplets": assigned_droplets[:2] if len(assigned_droplets) > 1 else assigned_droplets
                    },
                    "worker_email": {
                        "type": "worker",
                        "assigned_droplets": [assigned_droplets[0]]
                    },
                    "scheduler": {
                        "type": "worker", 
                        "assigned_droplets": [assigned_droplets[0]]
                    },
                    # Infrastructure services (always on master)
                    "database": {
                        "type": "infrastructure",
                        "port": self.get_hash_based_port(project, env, 5000, 1000),
                        "assigned_droplets": ["master"]
                    },
                    "redis": {
                        "type": "infrastructure",
                        "port": self.get_hash_based_port(project, env, 6000, 1000),
                        "assigned_droplets": ["master"]
                    },
                    "opensearch": {
                        "type": "infrastructure",
                        "port": self.get_hash_based_port(project, env, 9200, 200),
                        "assigned_droplets": ["master"]
                    },
                    "vault": {
                        "type": "infrastructure",
                        "port": self.get_hash_based_port(project, env, 8200, 200),
                        "assigned_droplets": ["master"]
                    }
                }
                
                required_services[project_key] = services
        
        return required_services
    
    # Droplet Management
    def add_droplet(self, name: str, ip: str, size: str, region: str, role: str, monitors: List[str] = None, project: str = None):
        """Add a new droplet to the state"""
        droplet_data = {
            "ip": ip,
            "size": size,
            "region": region,
            "role": role,
            "monitors": monitors or []
        }
        
        if project:
            droplet_data["project"] = project
            
        self.state["droplets"][name] = droplet_data
        self.save_state()
    
    def update_droplet_ip(self, name: str, new_ip: str):
        """Update droplet IP address"""
        if name in self.state["droplets"]:
            self.state["droplets"][name]["ip"] = new_ip
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
    
    # Project Management
    def add_project_service(self, project: str, service_type: str, port: int = None, 
                           assigned_droplets: List[str] = None, service_config: Dict[str, Any] = None):
        """Add a service to a project"""
        if project not in self.state["projects"]:
            self.state["projects"][project] = {}
        
        service_data = {
            "assigned_droplets": assigned_droplets or []
        }
        
        if port is not None:
            service_data["port"] = port
            
        if service_config:
            service_data.update(service_config)
        
        self.state["projects"][project][service_type] = service_data
        self.save_state()
    
    def remove_project_service(self, project: str, service_type: str):
        """Remove a service from a project"""
        if project in self.state["projects"] and service_type in self.state["projects"][project]:
            del self.state["projects"][project][service_type]
            self.save_state()
    
    def get_project_services(self, project: str) -> Dict[str, Dict[str, Any]]:
        """Get all services for a project"""
        return self.state["projects"].get(project, {})
    
    def get_all_projects(self) -> Dict[str, Dict[str, Any]]:
        """Get all projects"""
        return self.state["projects"]
    
    # Computed Relationships
    def get_service_name(self, project: str, service_type: str) -> str:
        """Generate service name from project and service type"""
        return f"{project}-{service_type}"
    
    def get_services_on_droplet(self, droplet_name: str) -> List[str]:
        """Get all services running on a specific droplet"""
        services = []
        for project, project_services in self.state["projects"].items():
            for service_type, service_config in project_services.items():
                if droplet_name in service_config.get("assigned_droplets", []):
                    services.append(self.get_service_name(project, service_type))
        return services
    
    def get_load_balancer_targets(self, project: str, service_type: str) -> List[str]:
        """Get load balancer targets for a service (web services only)"""
        service_config = self.state["projects"].get(project, {}).get(service_type, {})
        
        # Skip workers and infrastructure services - they don't need load balancing
        if service_config.get("type") in ["worker", "infrastructure"]:
            return []
        
        # Skip services without ports
        if "port" not in service_config:
            return []
        
        targets = []
        for droplet_name in service_config.get("assigned_droplets", []):
            droplet = self.get_droplet(droplet_name)
            if droplet:
                droplet_ip = droplet["ip"]
                port = service_config["port"]
                targets.append(f"{droplet_ip}:{port}")
        
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
    def update_heartbeat_config(self, primary_sender: str = None, backup_senders: List[str] = None, 
                               interval_minutes: int = None):
        """Update heartbeat monitoring configuration"""
        if primary_sender:
            self.state["health_monitoring"]["heartbeat_config"]["primary_sender"] = primary_sender
        if backup_senders is not None:
            self.state["health_monitoring"]["heartbeat_config"]["backup_senders"] = backup_senders
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
        for project, services in self.state["projects"].items():
            for service_type, service_config in services.items():
                for droplet_name in service_config.get("assigned_droplets", []):
                    if droplet_name not in self.state["droplets"]:
                        issues.append(f"Service {project}-{service_type} assigned to non-existent droplet {droplet_name}")
        
        # Check for duplicate ports on same droplet
        droplet_ports = {}
        for project, services in self.state["projects"].items():
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
        
        # Validate infrastructure spec consistency
        spec = self.get_infrastructure_spec()
        required_droplets = self.get_required_droplets()
        
        for name, required_config in required_droplets.items():
            current_droplet = self.get_droplet(name)
            if current_droplet:
                if current_droplet.get("size") != required_config.get("size"):
                    issues.append(f"Droplet {name} size mismatch: current={current_droplet.get('size')}, required={required_config.get('size')}")
                if current_droplet.get("role") != required_config.get("role"):
                    issues.append(f"Droplet {name} role mismatch: current={current_droplet.get('role')}, required={required_config.get('role')}")
        
        return issues
    
    def get_summary(self) -> Dict[str, Any]:
        """Get infrastructure summary"""
        droplet_count = len(self.state["droplets"])
        project_count = len(self.state["projects"])
        
        service_count = 0
        worker_count = 0
        web_service_count = 0
        infrastructure_service_count = 0
        
        for services in self.state["projects"].values():
            for service_type, service_config in services.items():
                service_count += 1
                service_type_info = service_config.get("type", "web")
                
                if service_type_info == "worker":
                    worker_count += 1
                elif service_type_info == "web":
                    web_service_count += 1
                elif service_type_info == "infrastructure":
                    infrastructure_service_count += 1
        
        spec = self.get_infrastructure_spec()
        projects_in_spec = len(spec.get("projects", {}))
        
        return {
            "droplets": droplet_count,
            "projects": project_count,
            "services": service_count,
            "worker_services": worker_count,
            "web_services": web_service_count,
            "infrastructure_services": infrastructure_service_count,
            "projects_in_spec": projects_in_spec,
            "master_ip": self.get_master_droplet()["ip"] if self.get_master_droplet() else None,
            "web_droplet_count": len(self.get_web_droplets()),
            "validation_issues": self.validate_state()
        }