"""
IP Detection and Validation Utilities

Provides utilities for detecting public IPs, extracting client IPs from requests,
validating IP addresses, and managing IP-based security operations.
"""

import ipaddress
import socket
from typing import Optional, List, Dict, Any, Union
from fastapi import Request
import requests


def detect_public_ip(timeout: int = 5, fallback_to_local: bool = False) -> Optional[str]:
    """
    Auto-detect the public IP address of this machine.
    
    Args:
        timeout: Timeout for each IP detection service call
        fallback_to_local: Whether to fallback to local IP detection
        
    Returns:
        Public IP address or None if detection fails
        
    Example:
        >>> ip = detect_public_ip()
        >>> print(f"Your public IP: {ip}")
    """
    # List of reliable IP detection services
    ip_services = [
        "https://ipinfo.io/ip",
        "https://api.ipify.org", 
        "https://checkip.amazonaws.com",
        "https://icanhazip.com",
        "https://ifconfig.me/ip",
        "https://api.my-ip.io/ip",
        "https://httpbin.org/ip"  # Returns JSON: {"origin": "ip"}
    ]
    
    for service in ip_services:
        try:
            response = requests.get(service, timeout=timeout)
            if response.status_code == 200:
                # Handle different response formats
                if service == "https://httpbin.org/ip":
                    import json
                    data = json.loads(response.text)
                    ip = data.get("origin", "").split(",")[0].strip()  # Handle multiple IPs
                else:
                    ip = response.text.strip()
                
                # Validate the detected IP
                if is_valid_ip(ip) and is_public_ip(ip):
                    return ip
                    
        except Exception:
            continue  # Try next service
    
    # Fallback: try using socket to detect local IP (won't work for NAT)
    if fallback_to_local:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Connect to a remote address (doesn't actually send data)
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
                if is_valid_ip(local_ip) and is_public_ip(local_ip):
                    return local_ip
        except Exception:
            pass
    
    return None


def get_client_ip(request: Request, trust_proxy: bool = True) -> Optional[str]:
    """
    Extract client IP from FastAPI request, handling proxies and load balancers.
    
    Args:
        request: FastAPI Request object
        trust_proxy: Whether to trust proxy headers (X-Forwarded-For, etc.)
        
    Returns:
        Client IP address or None if not detectable
        
    Example:
        @app.get("/")
        async def root(request: Request):
            client_ip = get_client_ip(request)
            return {"client_ip": client_ip}
    """
    if trust_proxy:
        # Check proxy headers in order of preference
        proxy_headers = [
            "X-Forwarded-For",      # Standard proxy header
            "X-Real-IP",            # Nginx proxy
            "CF-Connecting-IP",     # Cloudflare
            "X-Client-IP",          # General client IP
            "X-Cluster-Client-IP",  # Cluster environments
            "Forwarded"             # RFC 7239 standard
        ]
        
        for header in proxy_headers:
            if header in request.headers:
                # X-Forwarded-For can contain multiple IPs: "client, proxy1, proxy2"
                ip_list = request.headers[header].split(",")
                for ip in ip_list:
                    ip = ip.strip()
                    if is_valid_ip(ip) and not is_private_ip(ip):
                        return ip
    
    # Fallback to direct connection IP
    if hasattr(request, "client") and request.client:
        return request.client.host
    
    return None


def is_valid_ip(ip: str) -> bool:
    """
    Validate IP address format (supports both IPv4 and IPv6).
    
    Args:
        ip: IP address string to validate
        
    Returns:
        True if valid IP address, False otherwise
        
    Example:
        >>> is_valid_ip("192.168.1.1")
        True
        >>> is_valid_ip("invalid")
        False
    """
    if not ip:
        return False
        
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def is_public_ip(ip: str) -> bool:
    """
    Check if IP address is public (not private/reserved).
    
    Args:
        ip: IP address string to check
        
    Returns:
        True if IP is public, False if private/reserved
        
    Example:
        >>> is_public_ip("8.8.8.8")
        True
        >>> is_public_ip("192.168.1.1")
        False
    """
    if not is_valid_ip(ip):
        return False
    
    try:
        ip_obj = ipaddress.ip_address(ip)
        return not (ip_obj.is_private or ip_obj.is_reserved or ip_obj.is_loopback)
    except ValueError:
        return False


def is_private_ip(ip: str) -> bool:
    """
    Check if IP address is private (RFC 1918).
    
    Args:
        ip: IP address string to check
        
    Returns:
        True if IP is private, False otherwise
        
    Example:
        >>> is_private_ip("192.168.1.1")
        True
        >>> is_private_ip("8.8.8.8")
        False
    """
    if not is_valid_ip(ip):
        return False
    
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def validate_and_filter_ips(ip_list: List[str], 
                           allow_private: bool = False,
                           allow_loopback: bool = False) -> List[str]:
    """
    Validate and filter a list of IP addresses.
    
    Args:
        ip_list: List of IP address strings
        allow_private: Whether to include private IPs
        allow_loopback: Whether to include loopback IPs
        
    Returns:
        List of valid IP addresses
        
    Example:
        >>> ips = ["192.168.1.1", "8.8.8.8", "invalid", "127.0.0.1"]
        >>> validate_and_filter_ips(ips, allow_private=True)
        ['192.168.1.1', '8.8.8.8']
    """
    valid_ips = []
    
    for ip in ip_list:
        if not ip or not is_valid_ip(ip):
            continue
        
        try:
            ip_obj = ipaddress.ip_address(ip)
            
            # Skip based on filters
            if not allow_private and ip_obj.is_private:
                continue
            if not allow_loopback and ip_obj.is_loopback:
                continue
            if ip_obj.is_reserved:
                continue
                
            valid_ips.append(ip)
            
        except ValueError:
            continue
    
    return valid_ips


def get_authorized_ips(admin_ip_env: str = "ADMIN_IP",                      
                      additional_ips_env: str = "ADDITIONAL_IPS",
                      auto_detect: bool = True,
                      include_current_machine: bool = True) -> List[str]:
    """
    Get comprehensive list of authorized IPs from multiple sources.
    
    Args:
        admin_ip_env: Environment variable name for admin IP       
        additional_ips_env: Environment variable for comma-separated additional IPs
        auto_detect: Whether to auto-detect current machine's public IP
        include_current_machine: Whether to include current machine IP
        
    Returns:
        List of authorized IP addresses
        
    Example:
        >>> # Set environment: ADDITIONAL_IPS="203.0.113.1,203.0.113.2"
        >>> authorized = get_authorized_ips()
        >>> print(f"Authorized IPs: {authorized}")
    """
    import os
    
    authorized = []
    
    # 1. Explicit admin IP from environment
    admin_ip = os.getenv(admin_ip_env)
    if admin_ip:
        authorized.append(admin_ip)
    elif auto_detect and include_current_machine:
        # Auto-detect current machine's public IP
        detected_ip = detect_public_ip()
        if detected_ip:
            authorized.append(detected_ip)
            print(f"Auto-detected administrator IP: {detected_ip}")
       
    # 2. Additional IPs (comma-separated)
    additional_ips = os.getenv(additional_ips_env, "")
    if additional_ips:
        for ip in additional_ips.split(","):
            ip = ip.strip()
            if ip:
                authorized.append(ip)
    
    # Validate and filter
    return validate_and_filter_ips(authorized, allow_private=False)


def ip_in_range(ip: str, ip_range: str) -> bool:
    """
    Check if IP address is within a given IP range/subnet.
    
    Args:
        ip: IP address to check
        ip_range: IP range in CIDR notation (e.g., "192.168.1.0/24")
        
    Returns:
        True if IP is in range, False otherwise
        
    Example:
        >>> ip_in_range("192.168.1.100", "192.168.1.0/24")
        True
        >>> ip_in_range("10.0.0.1", "192.168.1.0/24")
        False
    """
    if not is_valid_ip(ip):
        return False
    
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(ip_range, strict=False)
    except ValueError:
        return False


def get_ip_info(ip: str, timeout: int = 5) -> Dict[str, Any]:
    """
    Get detailed information about an IP address using ipinfo.io.
    
    Args:
        ip: IP address to look up
        timeout: Request timeout in seconds
        
    Returns:
        Dictionary with IP information (country (2 letters), region, city, org, loc (lat/long), timezone (IANA identifier), hostname (reverse DNS is available), postal (zip if available))
        
        Example:
        {
            "ip": "185.220.100.252",
            "city": "Amsterdam", 
            "region": "North Holland",
            "country": "NL",
            "loc": "52.3740,4.8897",
            "timezone": "Europe/Amsterdam",
            "org": "AS31898 Oracle Corporation"
        }
        
    Example:
        >>> info = get_ip_info("8.8.8.8")
        >>> print(f"IP {ip} is from {info.get('country', 'Unknown')}")
    """
    if not is_valid_ip(ip):
        return {"error": "Invalid IP address"}
    
    try:
        response = requests.get(f"https://ipinfo.io/{ip}/json", timeout=timeout)
        if response.status_code == 200:
            return response.json()
        else:
            return {"error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def create_ip_whitelist_middleware(allowed_ips: List[str] = None, 
                                 auto_detect_admin: bool = True):
    """
    Create FastAPI middleware for IP-based access control.
    
    Args:
        allowed_ips: List of allowed IP addresses/ranges
        auto_detect_admin: Whether to auto-detect and allow admin IP
        
    Returns:
        FastAPI middleware function
        
    Example:
        from fastapi import FastAPI
        
        app = FastAPI()
        
        # Auto-detect admin IP and allow specific IPs
        whitelist_middleware = create_ip_whitelist_middleware(
            allowed_ips=["203.0.113.0/24"], 
            auto_detect_admin=True
        )
        app.middleware("http")(whitelist_middleware)
    """
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    
    # Build allowed IPs list
    if allowed_ips is None:
        allowed_ips = []
    
    if auto_detect_admin:
        admin_ips = get_authorized_ips()
        allowed_ips.extend(admin_ips)
    
    # Remove duplicates and validate
    allowed_ips = list(set(validate_and_filter_ips(allowed_ips, allow_private=True)))
    
    async def ip_whitelist_middleware(request: Request, call_next):
        client_ip = get_client_ip(request)
        
        if not client_ip:
            return JSONResponse(
                status_code=403,
                content={"error": "Cannot determine client IP"}
            )
        
        # Check if IP is allowed
        ip_allowed = False
        for allowed_ip in allowed_ips:
            if "/" in allowed_ip:  # CIDR range
                if ip_in_range(client_ip, allowed_ip):
                    ip_allowed = True
                    break
            else:  # Single IP
                if client_ip == allowed_ip:
                    ip_allowed = True
                    break
        
        if not ip_allowed:
            return JSONResponse(
                status_code=403,
                content={
                    "error": "Access denied",
                    "client_ip": client_ip,
                    "message": "Your IP address is not authorized"
                }
            )
        
        # IP is allowed, continue with request
        response = await call_next(request)
        return response
    
    return ip_whitelist_middleware


# Convenience functions for common use cases
def get_my_public_ip() -> Optional[str]:
    """Get current machine's public IP address."""
    return detect_public_ip()


def is_ip_safe_for_ssh(ip: str) -> bool:
    """Check if IP is safe for SSH access (public and valid)."""
    return is_valid_ip(ip) and is_public_ip(ip)


def format_ip_for_firewall(ip: str) -> str:
    """Format IP address for firewall rules (add /32 if needed)."""
    if not is_valid_ip(ip):
        raise ValueError(f"Invalid IP address: {ip}")
    
    if "/" not in ip:
        return f"{ip}/32"
    return ip