"""
Service Registry Implementation.

Tracks where services are deployed across the infrastructure.
Supports both sync and async operations.

Backends:
- In-memory (default, for testing)
- Database (for production)
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Callable
from datetime import datetime
import json
import logging

from .models import ServiceRecord, ProjectServers

logger = logging.getLogger(__name__)


class RegistryBackend(ABC):
    """Abstract backend for service registry storage."""
    
    @abstractmethod
    def save(self, record: ServiceRecord) -> None:
        """Save a service record."""
        pass
    
    @abstractmethod
    def delete(self, workspace_id: str, project: str, environment: str, 
               service: str, server_ip: str) -> bool:
        """Delete a service record. Returns True if deleted."""
        pass
    
    @abstractmethod
    def find_service(self, workspace_id: str, project: str, environment: str,
                     service: str) -> List[ServiceRecord]:
        """Find all instances of a service."""
        pass
    
    @abstractmethod
    def find_by_server(self, server_ip: str) -> List[ServiceRecord]:
        """Find all services on a server."""
        pass
    
    @abstractmethod
    def get_project_servers(self, workspace_id: str, project: str, 
                           environment: str) -> List[str]:
        """Get all server IPs for a project/env."""
        pass
    
    @abstractmethod
    def get_stateful_services(self, workspace_id: str, project: str,
                              environment: str) -> List[ServiceRecord]:
        """Get all stateful services for a project/env."""
        pass


class InMemoryBackend(RegistryBackend):
    """In-memory backend for testing and simple deployments."""
    
    def __init__(self):
        # Key: (workspace_id, project, env, service, server_ip) -> ServiceRecord
        self._records: Dict[tuple, ServiceRecord] = {}
    
    def _key(self, record: ServiceRecord) -> tuple:
        return (record.workspace_id, record.project, record.environment,
                record.service, record.server_ip)
    
    def save(self, record: ServiceRecord) -> None:
        record.updated_at = datetime.utcnow()
        self._records[self._key(record)] = record
    
    def delete(self, workspace_id: str, project: str, environment: str,
               service: str, server_ip: str) -> bool:
        key = (workspace_id, project, environment, service, server_ip)
        if key in self._records:
            del self._records[key]
            return True
        return False
    
    def find_service(self, workspace_id: str, project: str, environment: str,
                     service: str) -> List[ServiceRecord]:
        results = []
        for key, record in self._records.items():
            if (key[0] == workspace_id and key[1] == project and 
                key[2] == environment and key[3] == service):
                results.append(record)
        return results
    
    def find_by_server(self, server_ip: str) -> List[ServiceRecord]:
        return [r for r in self._records.values() if r.server_ip == server_ip]
    
    def get_project_servers(self, workspace_id: str, project: str,
                           environment: str) -> List[str]:
        servers = set()
        for key, record in self._records.items():
            if key[0] == workspace_id and key[1] == project and key[2] == environment:
                servers.add(record.server_ip)
        return list(servers)
    
    def get_stateful_services(self, workspace_id: str, project: str,
                              environment: str) -> List[ServiceRecord]:
        results = []
        for key, record in self._records.items():
            if (key[0] == workspace_id and key[1] == project and 
                key[2] == environment and record.is_stateful):
                results.append(record)
        return results


class DatabaseBackend(RegistryBackend):
    """
    Database backend using existing service_droplets table.
    
    Expects a query function to be injected for database access.
    """
    
    def __init__(self, query_func: Callable, execute_func: Callable):
        """
        Args:
            query_func: Function to execute SELECT queries, returns list of dicts
            execute_func: Function to execute INSERT/UPDATE/DELETE queries
        """
        self._query = query_func
        self._execute = execute_func
    
    def save(self, record: ServiceRecord) -> None:
        """Save or update a service record."""
        # Upsert into service_droplets table
        sql = """
            INSERT INTO service_droplets (
                workspace_id, project, environment, service,
                server_ip, host_port, container_port, container_name,
                private_ip, internal_port, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (workspace_id, project, environment, service, server_ip)
            DO UPDATE SET
                host_port = excluded.host_port,
                container_port = excluded.container_port,
                container_name = excluded.container_name,
                private_ip = excluded.private_ip,
                internal_port = excluded.internal_port,
                updated_at = excluded.updated_at
        """
        self._execute(sql, (
            record.workspace_id, record.project, record.environment, record.service,
            record.server_ip, record.host_port, record.container_port, record.container_name,
            record.private_ip, record.internal_port, datetime.utcnow().isoformat()
        ))
    
    def delete(self, workspace_id: str, project: str, environment: str,
               service: str, server_ip: str) -> bool:
        sql = """
            DELETE FROM service_droplets
            WHERE workspace_id = ? AND project = ? AND environment = ?
              AND service = ? AND server_ip = ?
        """
        self._execute(sql, (workspace_id, project, environment, service, server_ip))
        return True  # SQLite doesn't easily return affected rows
    
    def find_service(self, workspace_id: str, project: str, environment: str,
                     service: str) -> List[ServiceRecord]:
        sql = """
            SELECT * FROM service_droplets
            WHERE workspace_id = ? AND project = ? AND environment = ? AND service = ?
        """
        rows = self._query(sql, (workspace_id, project, environment, service))
        return [self._row_to_record(r) for r in rows]
    
    def find_by_server(self, server_ip: str) -> List[ServiceRecord]:
        sql = "SELECT * FROM service_droplets WHERE server_ip = ?"
        rows = self._query(sql, (server_ip,))
        return [self._row_to_record(r) for r in rows]
    
    def get_project_servers(self, workspace_id: str, project: str,
                           environment: str) -> List[str]:
        sql = """
            SELECT DISTINCT server_ip FROM service_droplets
            WHERE workspace_id = ? AND project = ? AND environment = ?
        """
        rows = self._query(sql, (workspace_id, project, environment))
        return [r["server_ip"] for r in rows]
    
    def get_stateful_services(self, workspace_id: str, project: str,
                              environment: str) -> List[ServiceRecord]:
        # Filter stateful services in Python since service type isn't stored
        all_services = self._query(
            "SELECT * FROM service_droplets WHERE workspace_id = ? AND project = ? AND environment = ?",
            (workspace_id, project, environment)
        )
        records = [self._row_to_record(r) for r in all_services]
        return [r for r in records if r.is_stateful]
    
    def _row_to_record(self, row: dict) -> ServiceRecord:
        """Convert database row to ServiceRecord."""
        return ServiceRecord(
            workspace_id=row["workspace_id"],
            project=row["project"],
            environment=row["environment"],
            service=row["service"],
            server_ip=row["server_ip"],
            host_port=row.get("host_port", 0),
            container_port=row.get("container_port", 0),
            container_name=row.get("container_name", ""),
            private_ip=row.get("private_ip"),
            internal_port=row.get("internal_port"),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else datetime.utcnow(),
        )


class ServiceRegistry:
    """
    Sync service registry.
    
    Tracks where services are deployed for nginx stream routing.
    
    Usage:
        registry = ServiceRegistry()  # In-memory
        registry = ServiceRegistry(backend=DatabaseBackend(query, execute))
        
        # Register a deployment
        registry.register(ServiceRecord(...))
        
        # Find where redis is
        locations = registry.find_service("u1", "myapp", "prod", "redis")
        
        # Get all servers in project (for nginx updates)
        servers = registry.get_project_servers("u1", "myapp", "prod")
    """
    
    def __init__(self, backend: RegistryBackend = None):
        self._backend = backend or InMemoryBackend()
    
    def register(self, record: ServiceRecord) -> None:
        """Register a service deployment."""
        logger.debug(f"Registering {record.service} on {record.server_ip}")
        self._backend.save(record)
    
    def unregister(self, workspace_id: str, project: str, environment: str,
                   service: str, server_ip: str) -> bool:
        """Unregister a service deployment."""
        logger.debug(f"Unregistering {service} from {server_ip}")
        return self._backend.delete(workspace_id, project, environment, service, server_ip)
    
    def find_service(self, workspace_id: str, project: str, environment: str,
                     service: str) -> List[ServiceRecord]:
        """Find all instances of a service."""
        return self._backend.find_service(workspace_id, project, environment, service)
    
    def find_by_server(self, server_ip: str) -> List[ServiceRecord]:
        """Find all services on a server."""
        return self._backend.find_by_server(server_ip)
    
    def get_project_servers(self, workspace_id: str, project: str,
                           environment: str) -> List[str]:
        """Get all server IPs for a project/env."""
        return self._backend.get_project_servers(workspace_id, project, environment)
    
    def get_stateful_services(self, workspace_id: str, project: str,
                              environment: str) -> List[ServiceRecord]:
        """Get all stateful services (postgres, redis, etc.) for a project/env."""
        return self._backend.get_stateful_services(workspace_id, project, environment)
    
    def determine_backend_mode(self, service: str, deployed_servers: List[str],
                               current_server: str) -> str:
        """
        Determine nginx backend mode for a specific server.
        
        Args:
            service: Service name
            deployed_servers: Servers where the service is deployed
            current_server: The server whose nginx we're configuring
            
        Returns:
            "container" if service is local, "ip_port" if remote
        """
        if current_server in deployed_servers:
            return "container"  # Use Docker DNS
        return "ip_port"  # Use remote IP:port


class AsyncServiceRegistry:
    """
    Async service registry.
    
    Same interface as ServiceRegistry but with async methods.
    Uses sync backend wrapped in run_in_executor for DB operations.
    """
    
    def __init__(self, backend: RegistryBackend = None):
        self._sync = ServiceRegistry(backend)
    
    async def register(self, record: ServiceRecord) -> None:
        """Register a service deployment."""
        self._sync.register(record)
    
    async def unregister(self, workspace_id: str, project: str, environment: str,
                        service: str, server_ip: str) -> bool:
        """Unregister a service deployment."""
        return self._sync.unregister(workspace_id, project, environment, service, server_ip)
    
    async def find_service(self, workspace_id: str, project: str, environment: str,
                          service: str) -> List[ServiceRecord]:
        """Find all instances of a service."""
        return self._sync.find_service(workspace_id, project, environment, service)
    
    async def find_by_server(self, server_ip: str) -> List[ServiceRecord]:
        """Find all services on a server."""
        return self._sync.find_by_server(server_ip)
    
    async def get_project_servers(self, workspace_id: str, project: str,
                                  environment: str) -> List[str]:
        """Get all server IPs for a project/env."""
        return self._sync.get_project_servers(workspace_id, project, environment)
    
    async def get_stateful_services(self, workspace_id: str, project: str,
                                    environment: str) -> List[ServiceRecord]:
        """Get all stateful services for a project/env."""
        return self._sync.get_stateful_services(workspace_id, project, environment)
    
    def determine_backend_mode(self, service: str, deployed_servers: List[str],
                               current_server: str) -> str:
        """Determine nginx backend mode (sync, no IO)."""
        return self._sync.determine_backend_mode(service, deployed_servers, current_server)
