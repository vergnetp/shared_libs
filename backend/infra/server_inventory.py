import json
from typing import List, Dict, Any, Optional
from pathlib import Path
from datetime import datetime
from logger import Logger
from do_manager import DOManager


def log(msg):
    Logger.log(msg)


class ServerInventory:
    """Manage server inventory in config/servers.json"""
    
    SERVERS_FILE = Path("config/servers.json")
    
    # Deployment status values
    STATUS_RESERVE = "reserve"      # In pool, available for claim
    STATUS_BLUE = "blue"           # Claimed for new deployment
    STATUS_GREEN = "green"         # Current production
    STATUS_DESTROYING = "destroying"  # Marked for cleanup
    
    @staticmethod
    def _load_inventory() -> Dict[str, Any]:
        """Load servers inventory from file"""
        if not ServerInventory.SERVERS_FILE.exists():
            return {"servers": []}
        
        try:
            return json.loads(ServerInventory.SERVERS_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log(f"Warning: Could not load servers.json: {e}")
            log("Using empty inventory")
            return {"servers": []}
    
    @staticmethod
    def _save_inventory(inventory: Dict[str, Any]):
        """Save servers inventory to file"""
        ServerInventory.SERVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        ServerInventory.SERVERS_FILE.write_text(json.dumps(inventory, indent=4))
    
    @staticmethod
    def add_servers(droplets_info: List[Dict[str, Any]], deployment_status: str = STATUS_RESERVE):
        """
        Add new servers to inventory.
        
        Args:
            droplets_info: List of droplet info dicts from DOManager
            deployment_status: Initial status (default: "reserve")
        """
        inventory = ServerInventory._load_inventory()
        
        for droplet in droplets_info:
            server_entry = {
                "droplet_id": droplet['droplet_id'],
                "name": droplet.get('name', 'unknown'),
                "ip": droplet['ip'],
                "private_ip": droplet['private_ip'],
                "zone": droplet['zone'],
                "cpu": droplet['cpu'],
                "memory": droplet['memory'],
                "deployment_status": deployment_status,
                "created": droplet['created']
            }
            
            inventory['servers'].append(server_entry)
            log(f"Added server {droplet['ip']} to inventory (status: {deployment_status})")
        
        ServerInventory._save_inventory(inventory)
    
    @staticmethod
    def get_servers(
        deployment_status: str = None,
        zone: str = None,
        cpu: int = None,
        memory: int = None
    ) -> List[Dict[str, Any]]:
        """
        Query servers from inventory with filters.
        
        Args:
            deployment_status: Filter by deployment status
            zone: Filter by region/zone
            cpu: Filter by CPU count
            memory: Filter by memory (MB)
            
        Returns:
            List of matching server entries
        """
        inventory = ServerInventory._load_inventory()
        servers = inventory['servers']
        
        # Apply filters
        if deployment_status is not None:
            servers = [s for s in servers if s.get('deployment_status') == deployment_status]
        
        if zone is not None:
            servers = [s for s in servers if s.get('zone') == zone]
        
        if cpu is not None:
            servers = [s for s in servers if s.get('cpu') == cpu]
        
        if memory is not None:
            servers = [s for s in servers if s.get('memory') == memory]
        
        return servers
    
    @staticmethod
    def get_server_by_ip(ip: str) -> Optional[Dict[str, Any]]:
        """Get server entry by IP address"""
        inventory = ServerInventory._load_inventory()
        
        for server in inventory['servers']:
            if server['ip'] == ip:
                return server
        
        return None
    
    @staticmethod
    def update_server_status(ips: List[str], new_status: str):
        """
        Update deployment status for multiple servers by IP.
        
        Args:
            ips: List of server IPs to update
            new_status: New deployment status
        """
        inventory = ServerInventory._load_inventory()
        
        updated_count = 0
        for server in inventory['servers']:
            if server['ip'] in ips:
                server['deployment_status'] = new_status
                updated_count += 1
                log(f"Updated {server['ip']} status to '{new_status}'")
        
        if updated_count > 0:
            ServerInventory._save_inventory(inventory)
            log(f"Updated {updated_count} servers to status '{new_status}'")
    
    @staticmethod
    def claim_servers(
        count: int,
        zone: str,
        cpu: int,
        memory: int
    ) -> List[str]:
        """
        Claim servers for deployment (reserve → blue).
        Creates new servers if not enough available.
        
        Args:
            count: Number of servers needed
            zone: Region/zone for servers
            cpu: CPU count
            memory: Memory in MB
            
        Returns:
            List of server IPs claimed
        """
        log(f"Claiming {count} servers ({cpu} CPU, {memory}MB RAM) in {zone}")
        Logger.start()
        
        # Find available reserve servers matching specs
        available = ServerInventory.get_servers(
            deployment_status=ServerInventory.STATUS_RESERVE,
            zone=zone,
            cpu=cpu,
            memory=memory
        )
        
        needed = count - len(available)
        
        if needed > 0:
            log(f"Need {needed} more servers, creating...")
            # Create new servers via DOManager
            new_droplets = DOManager.create_droplets(
                count=needed,
                region=zone,
                cpu=cpu,
                memory=memory,
                tags=["deployer", f"zone:{zone}"]
            )
            
            # Add to inventory as reserve
            ServerInventory.add_servers(new_droplets, ServerInventory.STATUS_RESERVE)
            available.extend([{
                'ip': d['ip'],
                'droplet_id': d['droplet_id'],
                'zone': d['zone'],
                'cpu': d['cpu'],
                'memory': d['memory']
            } for d in new_droplets])
        
        # Claim the required number of servers
        claimed = available[:count]
        claimed_ips = [s['ip'] for s in claimed]
        
        # Update status to blue
        ServerInventory.update_server_status(claimed_ips, ServerInventory.STATUS_BLUE)
        
        Logger.end()
        log(f"Claimed {count} servers: {claimed_ips}")
        
        return claimed_ips
    
    @staticmethod
    def promote_blue_to_green(blue_ips: List[str], project: str = None, env: str = None) -> List[str]:
        """
        Promote blue servers to green (blue → green, old green → reserve).
        
        Args:
            blue_ips: IPs of blue servers to promote
            project: Optional project name for filtering old greens
            env: Optional environment name for filtering old greens
            
        Returns:
            List of old green IPs (now reserve)
        """
        log(f"Promoting blue servers to green: {blue_ips}")
        Logger.start()
        
        # Get current green servers (to be replaced)
        old_greens = ServerInventory.get_servers(deployment_status=ServerInventory.STATUS_GREEN)
        old_green_ips = [s['ip'] for s in old_greens]
        
        # Demote old greens to reserve
        if old_green_ips:
            ServerInventory.update_server_status(old_green_ips, ServerInventory.STATUS_RESERVE)
            log(f"Demoted old greens to reserve: {old_green_ips}")
        
        # Promote blues to green
        ServerInventory.update_server_status(blue_ips, ServerInventory.STATUS_GREEN)
        log(f"Promoted blues to green: {blue_ips}")
        
        Logger.end()
        return old_green_ips
    
    @staticmethod
    def release_servers(ips: List[str], destroy: bool = False):
        """
        Release servers back to pool or destroy them.
        
        Args:
            ips: List of server IPs to release
            destroy: If True, destroy droplets; if False, return to reserve pool
        """
        if destroy:
            log(f"Destroying {len(ips)} servers...")
            Logger.start()
            
            inventory = ServerInventory._load_inventory()
            
            for ip in ips:
                server = ServerInventory.get_server_by_ip(ip)
                if server:
                    # Destroy droplet via DOManager
                    DOManager.destroy_droplet(server['droplet_id'])
                    
                    # Remove from inventory
                    inventory['servers'] = [s for s in inventory['servers'] if s['ip'] != ip]
                    log(f"Destroyed and removed server {ip}")
            
            ServerInventory._save_inventory(inventory)
            Logger.end()
        else:
            log(f"Releasing {len(ips)} servers to reserve pool")
            ServerInventory.update_server_status(ips, ServerInventory.STATUS_RESERVE)
    
    @staticmethod
    def sync_with_digitalocean():
        """
        Sync inventory with actual DigitalOcean state.
        Reconciles any discrepancies between servers.json and DO API.
        """
        log("Syncing inventory with DigitalOcean...")
        Logger.start()
        
        # Get all droplets from DO
        do_droplets = DOManager.list_droplets(tags=["deployer"])
        do_droplet_ids = {d['droplet_id'] for d in do_droplets}
        
        inventory = ServerInventory._load_inventory()
        local_droplet_ids = {s['droplet_id'] for s in inventory['servers']}
        
        # Find discrepancies
        only_in_do = do_droplet_ids - local_droplet_ids
        only_in_local = local_droplet_ids - do_droplet_ids
        
        if only_in_do:
            log(f"Found {len(only_in_do)} droplets in DO not in local inventory")
            # Add missing droplets to inventory
            missing = [d for d in do_droplets if d['droplet_id'] in only_in_do]
            ServerInventory.add_servers(missing, ServerInventory.STATUS_RESERVE)
        
        if only_in_local:
            log(f"Found {len(only_in_local)} servers in local inventory not in DO")
            # Remove from inventory (droplets were destroyed externally)
            inventory['servers'] = [
                s for s in inventory['servers'] 
                if s['droplet_id'] not in only_in_local
            ]
            ServerInventory._save_inventory(inventory)
            log(f"Removed {len(only_in_local)} servers from inventory")
        
        Logger.end()
        log("Inventory sync complete")
    
    @staticmethod
    def list_all_servers() -> List[Dict[str, Any]]:
        """List all servers in inventory"""
        inventory = ServerInventory._load_inventory()
        return inventory['servers']
    
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
    
    @staticmethod
    def get_server_by_ip(ip: str) -> Optional[Dict[str, Any]]:
        """
        Get server info by IP address.
        
        Args:
            ip: Server IP address
            
        Returns:
            Server info dict or None if not found
        """
        servers = ServerInventory.list_all_servers()
        
        for server in servers:
            if server.get('ip') == ip:
                return server
        
        return None