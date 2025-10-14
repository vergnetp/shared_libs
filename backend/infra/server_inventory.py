# server_inventory.py - Simplified version with only RESERVE and ACTIVE statuses

import json
from typing import List, Dict, Any, Optional
from logger import Logger
from do_manager import DOManager


def log(msg):
    Logger.log(msg)


class ServerInventory:
    """
    Simplified stateless server inventory - only RESERVE and ACTIVE statuses.
    
    RESERVE: Server is provisioned but not running any services
    ACTIVE: Server is running one or more services
    
    Source of truth for deployments is:
    - Nginx configs (which containers are routed to)
    - Running containers (docker ps)
    - Deployment state (deployments.json)
    """
    
    # Deployment status stored as droplet tags
    TAG_PREFIX = "Infra"
    STATUS_RESERVE = "reserve"
    STATUS_ACTIVE = "active"
    STATUS_DESTROYING = "destroying"
    
    @staticmethod
    def _get_deployment_status(droplet_tags: List[str]) -> str:
        """Extract deployment status from droplet tags"""
        for tag in droplet_tags:
            if tag.startswith("status:"):
                return tag.split(":", 1)[1]
        return ServerInventory.STATUS_RESERVE  # Default
    
    @staticmethod
    def _set_deployment_status(droplet_id: str, status: str):
        """
        Set deployment status via droplet tags.
        
        Args:
            droplet_id: Droplet ID
            status: Status value WITHOUT "status:" prefix (e.g., "active", not "status:active")
        """
        info = DOManager.get_droplet_info(droplet_id)
        current_tags = info.get('tags', [])
        
        # Find existing status tags to remove
        old_status_tags = [tag for tag in current_tags if tag.startswith("status:")]
        
        # Create new status tag (add prefix here)
        new_status_tag = f"status:{status}"
        
        # Update droplet tags: remove old status tags, add new one
        DOManager.update_droplet_tags(
            droplet_id, 
            add_tags=[new_status_tag],
            remove_tags=old_status_tags
        )
        
        log(f"Updated droplet {droplet_id} status to {status}")

    @staticmethod
    def get_servers(
        deployment_status: str = None,
        zone: str = None,
        cpu: int = None,
        memory: int = None
    ) -> List[Dict[str, Any]]:
        """Query servers directly from DigitalOcean API"""
        droplets = DOManager.list_droplets(tags=[ServerInventory.TAG_PREFIX])
        
        servers = []
        for droplet in droplets:
            # Parse deployment status from tags
            status = ServerInventory._get_deployment_status(droplet.get('tags', []))
            
            server = {
                'droplet_id': droplet['droplet_id'],
                'name': droplet.get('name', 'unknown'),
                'ip': droplet['ip'],
                'private_ip': droplet.get('private_ip'),
                'zone': droplet['zone'],
                'cpu': droplet['cpu'],
                'memory': droplet['memory'],
                'deployment_status': status
            }
            
            # Apply filters
            if deployment_status and status != deployment_status:
                continue
            if zone and droplet['zone'] != zone:
                continue
            if cpu and droplet['cpu'] != cpu:
                continue
            if memory and droplet['memory'] != memory:
                continue
            
            servers.append(server)
        
        return servers
    
    @staticmethod
    def update_server_status(ips: List[str], status: str):
        """Update status for multiple servers"""
        droplets = DOManager.list_droplets(tags=[ServerInventory.TAG_PREFIX])
        
        for droplet in droplets:
            if droplet['ip'] in ips:
                ServerInventory._set_deployment_status(droplet['droplet_id'], status)
    
    @staticmethod
    def add_servers(
        count: int,
        zone: str,
        cpu: int,
        memory: int,
        initial_status: str = None
    ) -> List[str]:
        """
        Create new servers and mark them with initial status.
        
        Args:
            count: Number of servers to create
            zone: Target zone
            cpu: CPU count
            memory: Memory in MB
            initial_status: Initial status (defaults to RESERVE)
            
        Returns:
            List of created server IPs
        """
        if initial_status is None:
            initial_status = ServerInventory.STATUS_RESERVE
        
        log(f"Creating {count} new servers ({cpu}CPU/{memory}MB) in {zone}")
        
        created_ips = DOManager.create_droplets(
            count=count,
            region=zone,
            cpu=cpu,
            memory=memory,
            tags=[ServerInventory.TAG_PREFIX, f"status:{initial_status}"]
        )
        
        return [d['ip'] for d in created_ips]
    
    @staticmethod
    def claim_servers(
        count: int,
        zone: str,
        cpu: int,
        memory: int
    ) -> List[str]:
        """
        Claim servers from reserve pool or create new ones.
        Marks claimed servers as ACTIVE.
        
        Args:
            count: Number of servers needed
            zone: Target zone
            cpu: CPU requirement
            memory: Memory requirement
            
        Returns:
            List of server IPs
        """
        log(f"Claiming {count} servers ({cpu} CPU, {memory}MB RAM) in {zone}")
        Logger.start()
        
        # Try to use existing reserve servers first
        reserve_servers = ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_RESERVE,
            zone=zone,
            cpu=cpu,
            memory=memory
        )
        
        # Also check for active servers (for shared deployments)
        active_servers = ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_ACTIVE,
            zone=zone,
            cpu=cpu,
            memory=memory
        )
        
        available_ips = [s['ip'] for s in reserve_servers] + [s['ip'] for s in active_servers]
        
        if len(available_ips) >= count:
            # Use existing servers
            claimed_ips = available_ips[:count]
            log(f"Using existing servers: {claimed_ips}")
        else:
            # Need to create more servers
            existing_count = len(available_ips)
            needed = count - existing_count
            
            log(f"Need {needed} more servers, creating...")
            new_ips = ServerInventory.add_servers(needed, zone, cpu, memory, ServerInventory.STATUS_RESERVE)
            claimed_ips = available_ips + new_ips
        
        # Mark claimed servers as ACTIVE
        ServerInventory.update_server_status(claimed_ips, ServerInventory.STATUS_ACTIVE)
        
        Logger.end()
        log(f"Claimed {count} servers: {claimed_ips}")
        
        return claimed_ips
    
    @staticmethod
    def release_servers(ips: List[str], destroy: bool = False):
        """Release servers back to pool or destroy them"""
        if destroy:
            log(f"Destroying {len(ips)} servers...")
            droplets = DOManager.list_droplets(tags=[ServerInventory.TAG_PREFIX])
            
            for droplet in droplets:
                if droplet['ip'] in ips:
                    DOManager.destroy_droplet(droplet['droplet_id'])
                    log(f"Destroyed server {droplet['ip']}")
        else:
            log(f"Releasing {len(ips)} servers to reserve pool")
            ServerInventory.update_server_status(ips, ServerInventory.STATUS_RESERVE)
    
    @staticmethod
    def sync_with_digitalocean():
        """No-op - we're always in sync since we query DO directly!"""
        log("Inventory is always synced (stateless design)")
        pass
    
    @staticmethod
    def list_all_servers() -> List[Dict[str, Any]]:
        """List all servers from DigitalOcean"""
        return ServerInventory.get_servers()
    
    @staticmethod
    def get_inventory_summary() -> Dict[str, int]:
        """Get summary of server counts by status"""
        servers = ServerInventory.list_all_servers()
        
        summary = {
            ServerInventory.STATUS_RESERVE: 0,
            ServerInventory.STATUS_ACTIVE: 0,
            ServerInventory.STATUS_DESTROYING: 0
        }
        
        for server in servers:
            status = server.get('deployment_status', ServerInventory.STATUS_RESERVE)
            summary[status] = summary.get(status, 0) + 1
        
        return summary