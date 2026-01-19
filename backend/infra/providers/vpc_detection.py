"""
VPC Detection Utility

Detects if the current machine is running inside a DigitalOcean VPC.
Used to determine whether to use private (VPC) or public IPs for inter-service communication.

Detection methods:
1. DO Metadata API (works inside Docker containers)
2. Fallback to socket-based detection

Performance: ~0.1ms (cached after first call)
"""

import socket
import functools
import urllib.request
from typing import Optional, List, Dict, Any


# DigitalOcean Metadata API base URL (available on all DO droplets)
DO_METADATA_BASE = "http://169.254.169.254/metadata/v1"
DO_METADATA_TIMEOUT = 0.5  # 500ms timeout


def _fetch_metadata(path: str) -> Optional[str]:
    """
    Fetch a value from DO Metadata API.
    
    Works inside Docker containers because it queries the host's metadata.
    Returns None if not on DigitalOcean or metadata unavailable.
    """
    try:
        url = f"{DO_METADATA_BASE}/{path}"
        req = urllib.request.Request(url, headers={"Metadata-Token": "droplet"})
        with urllib.request.urlopen(req, timeout=DO_METADATA_TIMEOUT) as resp:
            return resp.read().decode("utf-8").strip()
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def get_do_metadata() -> Dict[str, Any]:
    """
    Get DigitalOcean droplet metadata. Cached.
    
    Returns dict with:
        - droplet_id: int or None
        - public_ip: str or None  
        - private_ip: str or None (VPC IP)
        - region: str or None
        - is_digitalocean: bool
    
    Works inside Docker containers.
    """
    droplet_id = _fetch_metadata("id")
    
    if droplet_id is None:
        # Not on DigitalOcean
        return {
            "droplet_id": None,
            "public_ip": None,
            "private_ip": None,
            "region": None,
            "is_digitalocean": False,
        }
    
    return {
        "droplet_id": int(droplet_id),
        "public_ip": _fetch_metadata("interfaces/public/0/ipv4/address"),
        "private_ip": _fetch_metadata("interfaces/private/0/ipv4/address"),
        "region": _fetch_metadata("region"),
        "is_digitalocean": True,
    }


@functools.lru_cache(maxsize=1)
def get_current_droplet_id() -> Optional[int]:
    """
    Get the current droplet's ID.
    
    Returns:
        Droplet ID if running on DigitalOcean, None otherwise.
    
    Works inside Docker containers (uses DO Metadata API).
    """
    return get_do_metadata()["droplet_id"]


@functools.lru_cache(maxsize=1)
def get_local_ips() -> List[str]:
    """Get all local IP addresses. Cached."""
    ips = []
    
    # First try DO Metadata API (works inside Docker)
    metadata = get_do_metadata()
    if metadata["public_ip"]:
        ips.append(metadata["public_ip"])
    if metadata["private_ip"]:
        ips.append(metadata["private_ip"])
    
    # Fallback: socket-based detection
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    
    # Also try UDP connect trick
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if ip not in ips:
            ips.append(ip)
        s.close()
    except Exception:
        pass
    
    return ips


@functools.lru_cache(maxsize=1)
def get_vpc_ip() -> Optional[str]:
    """
    Get this machine's VPC private IP if running in a DO VPC.
    Returns None if not in a VPC.
    
    DigitalOcean VPCs use 10.x.x.x ranges.
    """
    # First try DO Metadata API (works inside Docker)
    metadata = get_do_metadata()
    if metadata["private_ip"]:
        return metadata["private_ip"]
    
    # Fallback: check local IPs for 10.x.x.x
    for ip in get_local_ips():
        if ip.startswith("10."):
            return ip
    return None


@functools.lru_cache(maxsize=1)
def is_in_vpc() -> bool:
    """
    Check if this machine is running inside a DigitalOcean VPC.
    
    Returns:
        True if running in VPC (has 10.x.x.x IP), False otherwise
    
    Performance: ~0.1ms (cached after first call)
    """
    return get_vpc_ip() is not None


def get_best_ip_for_target(
    public_ip: str,
    private_ip: Optional[str],
    target_droplet_id: Optional[int] = None,
) -> str:
    """
    Get the best IP to use for connecting to a target server.
    
    Routing priority:
    1. Same droplet → 127.0.0.1 (instant, <1ms)
    2. In VPC + target has private IP → private IP (low latency, no bandwidth cost)
    3. Otherwise → public IP
    
    Args:
        public_ip: Target's public IP
        private_ip: Target's VPC private IP (if any)
        target_droplet_id: Target's droplet ID (for same-server detection)
    
    Returns:
        Best IP to use for connection
    """
    # Same droplet? Use private IP (works from Docker containers)
    # Note: 127.0.0.1 doesn't work from Docker - container's localhost != host's localhost
    if target_droplet_id is not None:
        current_id = get_current_droplet_id()
        if current_id is not None and current_id == target_droplet_id:
            # Prefer private IP (accessible from Docker), fall back to public
            if private_ip:
                return private_ip
            return public_ip
    
    # In VPC with private IP available? Use VPC routing
    if private_ip and is_in_vpc():
        return private_ip
    
    # Fallback to public IP
    return public_ip


def get_routing_debug_info(
    target_public_ip: str,
    target_private_ip: Optional[str] = None,
    target_droplet_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Get debug information about routing decisions.
    
    Useful for troubleshooting connectivity issues.
    """
    metadata = get_do_metadata()
    current_id = get_current_droplet_id()
    in_vpc = is_in_vpc()
    vpc_ip = get_vpc_ip()
    
    # Determine routing
    is_same_droplet = (
        target_droplet_id is not None 
        and current_id is not None 
        and current_id == target_droplet_id
    )
    would_use_vpc = target_private_ip is not None and in_vpc and not is_same_droplet
    would_use_same_droplet_private = is_same_droplet and target_private_ip is not None
    
    best_ip = get_best_ip_for_target(target_public_ip, target_private_ip, target_droplet_id)
    
    return {
        # Current machine info
        "is_digitalocean": metadata["is_digitalocean"],
        "current_droplet_id": current_id,
        "current_public_ip": metadata["public_ip"],
        "current_private_ip": metadata["private_ip"],
        "current_region": metadata["region"],
        "is_in_vpc": in_vpc,
        "vpc_ip": vpc_ip,
        "local_ips": get_local_ips(),
        
        # Target info
        "target_droplet_id": target_droplet_id,
        "target_public_ip": target_public_ip,
        "target_private_ip": target_private_ip,
        
        # Routing decision
        "is_same_droplet": is_same_droplet,
        "would_use_same_droplet_private": would_use_same_droplet_private,
        "would_use_vpc": would_use_vpc,
        "best_ip": best_ip,
        "routing_reason": (
            "same_droplet_private_ip" if is_same_droplet else
            "vpc_private_ip" if would_use_vpc else
            "public_ip"
        ),
    }


# Pre-warm cache on import (optional, makes first call instant)
try:
    _cached = get_do_metadata()
except Exception:
    pass
