"""
DigitalOcean Management

Handles droplet lifecycle, firewall management, SSH keys, and snapshots.
Integrates with DigitalOcean API for infrastructure provisioning.
"""

import os
import time
import digitalocean
from typing import List, Dict, Optional, Any
from pathlib import Path


class DigitalOceanManager:
    """
    Manages DigitalOcean droplets, firewalls, SSH keys, and snapshots
    """
    
    def __init__(self, api_token: str = None):
        self.api_token = api_token or os.getenv('DO_TOKEN')
        if not self.api_token:
            raise ValueError("DigitalOcean API token required (DO_TOKEN environment variable)")
        
        self.manager = digitalocean.Manager(token=self.api_token)
        self.authorized_ips = self._get_authorized_ips()
    
    def _get_authorized_ips(self) -> List[str]:
        """Get list of authorized IPs for SSH access"""
        authorized = []
        
        # Administrator IPs
        admin_ip = os.getenv("ADMIN_IP")
        office_ip = os.getenv("OFFICE_IP")
        
        if admin_ip:
            authorized.append(admin_ip)
        if office_ip:
            authorized.append(office_ip)
        
        return authorized
    
    def add_droplet_ips_to_authorized(self, droplet_ips: List[str]):
        """Add droplet IPs to authorized list for inter-droplet communication"""
        for ip in droplet_ips:
            if ip not in self.authorized_ips:
                self.authorized_ips.append(ip)
    
    def create_droplet(self, name: str, size: str = "s-1vcpu-1gb", region: str = "lon1", 
                      image: str = "ubuntu-22-04-x64", ssh_keys: List[str] = None) -> digitalocean.Droplet:
        """Create a new droplet"""
        
        droplet = digitalocean.Droplet(
            token=self.api_token,
            name=name,
            region=region,
            image=image,
            size_slug=size,
            ssh_keys=ssh_keys or [],
            backups=True,
            ipv6=False,
            user_data=self._get_user_data_script()
        )
        
        droplet.create()
        
        # Wait for droplet to be active
        print(f"Creating droplet {name}...")
        while droplet.status != 'active':
            time.sleep(10)
            droplet.load()
            print(f"Droplet {name} status: {droplet.status}")
        
        print(f"Droplet {name} created with IP: {droplet.ip_address}")
        
        # Update authorized IPs to include new droplet
        self.add_droplet_ips_to_authorized([droplet.ip_address])
        
        # Setup firewall for new droplet
        self.setup_droplet_firewall(droplet.ip_address)
        
        return droplet
    
    def _get_user_data_script(self) -> str:
        """Get cloud-init user data script for droplet initialization"""
        return """#!/bin/bash
        
        # Update system
        apt-get update
        apt-get upgrade -y
        
        # Install Docker
        curl -fsSL https://get.docker.com -o get-docker.sh
        sh get-docker.sh
        
        # Install Docker Compose
        curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        chmod +x /usr/local/bin/docker-compose
        
        # Enable Docker service
        systemctl enable docker
        systemctl start docker
        
        # Install useful tools
        apt-get install -y htop curl wget jq unzip
        
        # Create app directory
        mkdir -p /opt/app
        chown root:root /opt/app
        
        # Setup log rotation
        cat > /etc/logrotate.d/docker-containers << EOF
/var/lib/docker/containers/*/*.log {
    rotate 5
    daily
    compress
    size=10M
    missingok
    delaycompress
    copytruncate
}
EOF
        """
    
    def get_droplet_by_name(self, name: str) -> Optional[digitalocean.Droplet]:
        """Get droplet by name"""
        droplets = self.manager.get_all_droplets()
        for droplet in droplets:
            if droplet.name == name:
                return droplet
        return None
    
    def get_droplet_by_ip(self, ip: str) -> Optional[digitalocean.Droplet]:
        """Get droplet by IP address"""
        droplets = self.manager.get_all_droplets()
        for droplet in droplets:
            if droplet.ip_address == ip:
                return droplet
        return None
    
    def destroy_droplet(self, name: str) -> bool:
        """Destroy a droplet by name"""
        droplet = self.get_droplet_by_name(name)
        if droplet:
            droplet.destroy()
            print(f"Droplet {name} destroyed")
            return True
        return False
    
    def create_snapshot(self, droplet_name: str, snapshot_name: str) -> Optional[str]:
        """Create a snapshot of a droplet"""
        droplet = self.get_droplet_by_name(droplet_name)
        if not droplet:
            print(f"Droplet {droplet_name} not found")
            return None
        
        # Power off droplet before snapshot
        if droplet.status == 'active':
            droplet.power_off()
            while droplet.status != 'off':
                time.sleep(10)
                droplet.load()
        
        # Create snapshot
        droplet.take_snapshot(snapshot_name)
        print(f"Snapshot {snapshot_name} created for droplet {droplet_name}")
        
        # Power back on
        droplet.power_on()
        
        return snapshot_name
    
    def create_from_snapshot(self, snapshot_name: str, new_droplet_name: str, 
                           size: str = "s-1vcpu-1gb", region: str = "lon1", 
                           ssh_keys: List[str] = None) -> digitalocean.Droplet:
        """Create a new droplet from a snapshot"""
        
        # Find snapshot
        snapshots = self.manager.get_all_snapshots()
        snapshot = None
        for s in snapshots:
            if s.name == snapshot_name:
                snapshot = s
                break
        
        if not snapshot:
            raise ValueError(f"Snapshot {snapshot_name} not found")
        
        # Create droplet from snapshot
        droplet = digitalocean.Droplet(
            token=self.api_token,
            name=new_droplet_name,
            region=region,
            image=snapshot.id,
            size_slug=size,
            ssh_keys=ssh_keys or [],
            backups=True
        )
        
        droplet.create()
        
        # Wait for droplet to be active
        print(f"Creating droplet {new_droplet_name} from snapshot {snapshot_name}...")
        while droplet.status != 'active':
            time.sleep(10)
            droplet.load()
        
        print(f"Droplet {new_droplet_name} created from snapshot with IP: {droplet.ip_address}")
        
        # Setup firewall
        self.setup_droplet_firewall(droplet.ip_address)
        
        return droplet
    
    def setup_droplet_firewall(self, droplet_ip: str):
        """Configure firewall rules for a droplet"""
        
        # Get all current droplet IPs for inter-droplet communication
        droplets = self.manager.get_all_droplets()
        all_droplet_ips = [d.ip_address for d in droplets if d.ip_address]
        
        # Combine authorized IPs with droplet IPs
        all_authorized_ips = self.authorized_ips + all_droplet_ips
        
        firewall_rules = self._generate_firewall_rules(all_authorized_ips)
        
        # Create firewall
        firewall_name = f"fw-{droplet_ip.replace('.', '-')}"
        
        # Check if firewall already exists
        firewalls = self.manager.get_all_firewalls()
        existing_firewall = None
        for fw in firewalls:
            if fw.name == firewall_name:
                existing_firewall = fw
                break
        
        if existing_firewall:
            # Update existing firewall
            existing_firewall.inbound_rules = firewall_rules['inbound']
            existing_firewall.outbound_rules = firewall_rules['outbound']
            existing_firewall.save()
        else:
            # Create new firewall
            firewall = digitalocean.Firewall(
                token=self.api_token,
                name=firewall_name,
                inbound_rules=firewall_rules['inbound'],
                outbound_rules=firewall_rules['outbound'],
                droplet_ids=[self.get_droplet_by_ip(droplet_ip).id]
            )
            firewall.create()
        
        print(f"Firewall configured for droplet {droplet_ip}")
    
    def _generate_firewall_rules(self, authorized_ips: List[str]) -> Dict[str, List[Dict]]:
        """Generate firewall rules for a droplet"""
        
        inbound_rules = []
        
        # SSH access for authorized IPs
        for ip in authorized_ips:
            if ip:  # Skip empty IPs
                inbound_rules.append({
                    'protocol': 'tcp',
                    'ports': '22',
                    'sources': {
                        'addresses': [ip if '/' in ip else f"{ip}/32"]
                    }
                })
        
        # Web traffic (public access through nginx)
        inbound_rules.extend([
            {
                'protocol': 'tcp',
                'ports': '80',
                'sources': {
                    'addresses': ['0.0.0.0/0', '::/0']
                }
            },
            {
                'protocol': 'tcp',
                'ports': '443',
                'sources': {
                    'addresses': ['0.0.0.0/0', '::/0']
                }
            },
            {
                'protocol': 'tcp',
                'ports': '8000-9999',  # Service ports
                'sources': {
                    'addresses': ['0.0.0.0/0', '::/0']
                }
            }
        ])
        
        # Internal services (droplet-only access)
        droplet_sources = [ip if '/' in ip else f"{ip}/32" for ip in authorized_ips if ip]
        
        if droplet_sources:
            inbound_rules.extend([
                {
                    'protocol': 'tcp',
                    'ports': '5432',  # PostgreSQL
                    'sources': {
                        'addresses': droplet_sources
                    }
                },
                {
                    'protocol': 'tcp',
                    'ports': '6379',  # Redis
                    'sources': {
                        'addresses': droplet_sources
                    }
                },
                {
                    'protocol': 'tcp',
                    'ports': '8200',  # Vault
                    'sources': {
                        'addresses': droplet_sources
                    }
                }
            ])
        
        # Outbound rules (allow all)
        outbound_rules = [
            {
                'protocol': 'tcp',
                'ports': 'all',
                'destinations': {
                    'addresses': ['0.0.0.0/0', '::/0']
                }
            },
            {
                'protocol': 'udp',
                'ports': 'all',
                'destinations': {
                    'addresses': ['0.0.0.0/0', '::/0']
                }
            }
        ]
        
        return {
            'inbound': inbound_rules,
            'outbound': outbound_rules
        }
    
    def update_administrator_ip(self, old_ip: str, new_ip: str):
        """Update administrator IP across all droplet firewalls"""
        
        # Update local authorized IPs list
        if old_ip in self.authorized_ips:
            self.authorized_ips.remove(old_ip)
        if new_ip not in self.authorized_ips:
            self.authorized_ips.append(new_ip)
        
        # Update all droplet firewalls
        droplets = self.manager.get_all_droplets()
        for droplet in droplets:
            if droplet.ip_address:
                self.setup_droplet_firewall(droplet.ip_address)
        
        print(f"Administrator IP updated from {old_ip} to {new_ip} across all droplets")
    
    def get_infrastructure_summary(self) -> Dict[str, Any]:
        """Get summary of DigitalOcean infrastructure"""
        droplets = self.manager.get_all_droplets()
        snapshots = self.manager.get_all_snapshots()
        firewalls = self.manager.get_all_firewalls()
        
        return {
            "droplet_count": len(droplets),
            "active_droplets": len([d for d in droplets if d.status == 'active']),
            "snapshot_count": len(snapshots),
            "firewall_count": len(firewalls),
            "authorized_ip_count": len(self.authorized_ips),
            "droplets": [
                {
                    "name": d.name,
                    "ip": d.ip_address,
                    "status": d.status,
                    "size": d.size_slug,
                    "region": d.region['name']
                }
                for d in droplets
            ]
        }
