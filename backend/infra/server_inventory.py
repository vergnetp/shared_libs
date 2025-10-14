# server_inventory_v2.py - Stateless version that doesn't use servers.json

import json
from typing import List, Dict, Any, Optional
from logger import Logger
from do_manager import DOManager


def log(msg):
    Logger.log(msg)


class ServerInventory:
    """
    Stateless server inventory - always queries DigitalOcean directly.
    No more servers.json file to get out of sync!
    """
    
    # Deployment status stored as droplet tags
    TAG_PREFIX = "Infra"
    STATUS_RESERVE = "reserve"
    STATUS_BLUE = "blue"
    STATUS_GREEN = "green"
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
            status: Status value WITHOUT "status:" prefix (e.g., "blue", not "status:blue")
        """
        info = DOManager.get_droplet_info(droplet_id)
        current_tags = info.get('tags', [])
        
        # Find existing status tags to remove
        old_status_tags = [tag for tag in current_tags if tag.startswith("status:")]
        
        # Keep non-status tags
        keep_tags = [tag for tag in current_tags if not tag.startswith("status:")]
        
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
        # Get all droplets with tag
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
                'deployment_status': status,
                'created': droplet.get('created'),
                'tags': droplet.get('tags', [])
            }
            
            # Apply filters
            if deployment_status and status != deployment_status:
                continue
            if zone and server['zone'] != zone:
                continue
            if cpu is not None and server['cpu'] != cpu:
                continue
            if memory is not None and server['memory'] != memory:
                continue
            
            servers.append(server)
        
        return servers
    
    @staticmethod
    def add_servers(droplets_info: List[Dict[str, Any]], deployment_status: str = STATUS_RESERVE):
        """Add deployment status tags to new servers"""
        for droplet in droplets_info:
            # Extract just the status value (e.g., "reserve" from "status:reserve")
            status_value = deployment_status.split(":", 1)[1] if ":" in deployment_status else deployment_status
            
            # Add base tags
            tags = [ServerInventory.TAG_PREFIX, f"zone:{droplet['zone']}"]
            
            DOManager.update_droplet_tags(
                droplet['droplet_id'],
                add_tags=tags,
                remove_tags=[]
            )
            
            # Set deployment status separately using the fixed method
            ServerInventory._set_deployment_status(droplet['droplet_id'], status_value)
            
            log(f"Tagged server {droplet['ip']} with status {status_value}")

    @staticmethod
    def update_server_status(ips: List[str], new_status: str):
        """Update deployment status for servers by IP"""
        # Extract status value without prefix
        status_value = new_status.split(":", 1)[1] if ":" in new_status else new_status
        
        # Get all droplets
        droplets = DOManager.list_droplets(tags=[ServerInventory.TAG_PREFIX])
        
        for droplet in droplets:
            if droplet['ip'] in ips:
                ServerInventory._set_deployment_status(droplet['droplet_id'], status_value)
    
    @staticmethod
    def claim_servers(
        count: int,
        zone: str,
        cpu: int,
        memory: int
    ) -> List[str]:
        """Claim servers for deployment (reserve → blue)"""
        log(f"Claiming {count} servers ({cpu} CPU, {memory}MB RAM) in {zone}")
        Logger.start()
        
        # Find available reserve servers
        available = ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_RESERVE,
            zone=zone,
            cpu=cpu,
            memory=memory
        )
        
        needed = count - len(available)
        
        if needed > 0:
            log(f"Need {needed} more servers, creating...")
            # Create new servers
            new_droplets = DOManager.create_droplets(
                count=needed,
                region=zone,
                cpu=cpu,
                memory=memory,
                tags=[ServerInventory.TAG_PREFIX, f"zone:{zone}", f"status:{ServerInventory.STATUS_RESERVE}"]
            )
            
            # Add to available list
            for d in new_droplets:
                available.append({
                    'ip': d['ip'],
                    'droplet_id': d['droplet_id']
                })
        
        # Claim required number
        claimed = available[:count]
        claimed_ips = [s['ip'] for s in claimed]
        
        # Update status to blue
        ServerInventory.update_server_status(claimed_ips, ServerInventory.STATUS_BLUE)
        
        Logger.end()
        log(f"Claimed {count} servers: {claimed_ips}")
        
        return claimed_ips
    
    @staticmethod
    def promote_blue_to_green(blue_ips: List[str], project: str = None, env: str = None) -> List[str]:
        """Promote blue servers to green"""
        log(f"Promoting blue servers to green: {blue_ips}")
        
        # Get current greens (will become reserve)
        old_greens = ServerInventory.get_servers(deployment_status=ServerInventory.STATUS_GREEN)
        old_green_ips = [s['ip'] for s in old_greens]
        
        # Demote old greens
        if old_green_ips:
            ServerInventory.update_server_status(old_green_ips, ServerInventory.STATUS_RESERVE)
        
        # Promote blues
        ServerInventory.update_server_status(blue_ips, ServerInventory.STATUS_GREEN)
        
        return old_green_ips
    
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
            ServerInventory.STATUS_BLUE: 0,
            ServerInventory.STATUS_GREEN: 0,
            ServerInventory.STATUS_DESTROYING: 0
        }
        
        for server in servers:
            status = server.get('deployment_status', ServerInventory.STATUS_RESERVE)
            summary[status] = summary.get(status, 0) + 1
        
        return summary