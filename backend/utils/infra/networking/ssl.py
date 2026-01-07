"""
SSL Certificate Manager - Let's Encrypt and certificate handling.

Handles:
- Let's Encrypt certificate generation via certbot
- Certificate renewal
- Self-signed certificates for development
- Certificate status monitoring
"""

from __future__ import annotations
import os
import subprocess
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, Any, List, Optional
from datetime import datetime, timedelta
from pathlib import Path

if TYPE_CHECKING:
    from ..context import DeploymentContext
    from ..ssh.client import SSHClient

from ..core.result import Result


@dataclass
class Certificate:
    """SSL certificate info."""
    domain: str
    issuer: str
    valid_from: datetime
    valid_until: datetime
    path: str
    key_path: str
    
    @property
    def is_valid(self) -> bool:
        now = datetime.utcnow()
        return self.valid_from <= now <= self.valid_until
    
    @property
    def days_until_expiry(self) -> int:
        return (self.valid_until - datetime.utcnow()).days
    
    @property
    def needs_renewal(self) -> bool:
        return self.days_until_expiry < 30
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "issuer": self.issuer,
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "days_until_expiry": self.days_until_expiry,
            "is_valid": self.is_valid,
            "needs_renewal": self.needs_renewal,
            "path": self.path,
            "key_path": self.key_path,
        }


class SSLManager:
    """
    SSL certificate manager.
    
    Usage:
        ssl = SSLManager(ctx)
        
        # Get Let's Encrypt certificate
        result = ssl.obtain_certificate(
            domain="api.example.com",
            email="admin@example.com",
        )
        
        # Renew certificates
        ssl.renew_certificates()
        
        # Create self-signed for dev
        ssl.create_self_signed("localhost")
    """
    
    CERT_DIR = "/etc/letsencrypt/live"
    SELF_SIGNED_DIR = "/etc/ssl/self-signed"
    
    def __init__(
        self, 
        ctx: 'DeploymentContext',
        ssh: Optional['SSHClient'] = None,
    ):
        self.ctx = ctx
        self.ssh = ssh
    
    def _exec(
        self, 
        cmd: str, 
        server: Optional[str] = None,
    ) -> tuple[int, str, str]:
        """Execute command locally or remotely."""
        if server and self.ssh:
            return self.ssh.exec(server, cmd)
        else:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=300
            )
            return result.returncode, result.stdout, result.stderr
    
    # =========================================================================
    # Let's Encrypt
    # =========================================================================
    
    def obtain_certificate(
        self,
        domain: str,
        email: str,
        server: Optional[str] = None,
        webroot: Optional[str] = None,
        standalone: bool = False,
        staging: bool = False,
    ) -> Result:
        """
        Obtain Let's Encrypt certificate.
        
        Args:
            domain: Domain name (can be comma-separated for multiple)
            email: Contact email for Let's Encrypt
            server: Remote server (None = local)
            webroot: Webroot path for HTTP-01 challenge
            standalone: Use standalone mode (stops nginx temporarily)
            staging: Use Let's Encrypt staging environment
            
        Returns:
            Result with certificate paths
        """
        # Build certbot command
        domains = domain.split(",")
        domain_args = " ".join(f"-d {d.strip()}" for d in domains)
        
        cmd = f"certbot certonly --non-interactive --agree-tos --email {email} {domain_args}"
        
        if staging:
            cmd += " --staging"
        
        if standalone:
            cmd += " --standalone"
        elif webroot:
            cmd += f" --webroot -w {webroot}"
        else:
            # Default: use nginx plugin
            cmd += " --nginx"
        
        self.ctx.log_info(f"Obtaining certificate for {domain}", server=server)
        
        code, stdout, stderr = self._exec(cmd, server)
        
        if code == 0:
            primary_domain = domains[0].strip()
            cert_path = f"{self.CERT_DIR}/{primary_domain}/fullchain.pem"
            key_path = f"{self.CERT_DIR}/{primary_domain}/privkey.pem"
            
            return Result.ok(
                f"Certificate obtained for {domain}",
                domain=primary_domain,
                cert_path=cert_path,
                key_path=key_path,
            )
        else:
            return Result.fail(
                stderr.strip() or stdout.strip() or "Failed to obtain certificate"
            )
    
    def renew_certificates(
        self,
        server: Optional[str] = None,
        force: bool = False,
    ) -> Result:
        """
        Renew all Let's Encrypt certificates.
        
        Args:
            server: Remote server
            force: Force renewal even if not needed
            
        Returns:
            Result with renewal status
        """
        cmd = "certbot renew --non-interactive"
        if force:
            cmd += " --force-renewal"
        
        self.ctx.log_info("Renewing certificates", server=server)
        
        code, stdout, stderr = self._exec(cmd, server)
        
        if code == 0:
            return Result.ok("Certificates renewed", output=stdout)
        else:
            return Result.fail(stderr.strip() or "Renewal failed")
    
    def revoke_certificate(
        self,
        domain: str,
        server: Optional[str] = None,
    ) -> Result:
        """Revoke a Let's Encrypt certificate."""
        cert_path = f"{self.CERT_DIR}/{domain}/cert.pem"
        
        code, stdout, stderr = self._exec(
            f"certbot revoke --cert-path {cert_path} --non-interactive",
            server
        )
        
        if code == 0:
            return Result.ok(f"Certificate revoked for {domain}")
        else:
            return Result.fail(stderr.strip())
    
    # =========================================================================
    # Self-Signed
    # =========================================================================
    
    def create_self_signed(
        self,
        domain: str,
        server: Optional[str] = None,
        days: int = 365,
        output_dir: Optional[str] = None,
    ) -> Result:
        """
        Create self-signed certificate for development.
        
        Args:
            domain: Domain name
            server: Remote server
            days: Validity in days
            output_dir: Output directory
            
        Returns:
            Result with certificate paths
        """
        output_dir = output_dir or self.SELF_SIGNED_DIR
        cert_path = f"{output_dir}/{domain}.crt"
        key_path = f"{output_dir}/{domain}.key"
        
        # Ensure directory exists
        self._exec(f"mkdir -p {output_dir}", server)
        
        # Generate self-signed certificate
        cmd = (
            f"openssl req -x509 -nodes -days {days} -newkey rsa:2048 "
            f"-keyout {key_path} -out {cert_path} "
            f'-subj "/CN={domain}/O=Self-Signed/C=US"'
        )
        
        code, stdout, stderr = self._exec(cmd, server)
        
        if code == 0:
            return Result.ok(
                f"Self-signed certificate created for {domain}",
                cert_path=cert_path,
                key_path=key_path,
            )
        else:
            return Result.fail(stderr.strip())
    
    # =========================================================================
    # Certificate Info
    # =========================================================================
    
    def get_certificate_info(
        self,
        domain: str,
        server: Optional[str] = None,
    ) -> Optional[Certificate]:
        """
        Get certificate information.
        
        Args:
            domain: Domain name
            server: Remote server
            
        Returns:
            Certificate object or None
        """
        cert_path = f"{self.CERT_DIR}/{domain}/fullchain.pem"
        key_path = f"{self.CERT_DIR}/{domain}/privkey.pem"
        
        # Check if cert exists
        code, _, _ = self._exec(f"test -f {cert_path}", server)
        if code != 0:
            # Try self-signed location
            cert_path = f"{self.SELF_SIGNED_DIR}/{domain}.crt"
            key_path = f"{self.SELF_SIGNED_DIR}/{domain}.key"
            code, _, _ = self._exec(f"test -f {cert_path}", server)
            if code != 0:
                return None
        
        # Get certificate details
        cmd = f"openssl x509 -in {cert_path} -noout -dates -issuer"
        code, stdout, stderr = self._exec(cmd, server)
        
        if code != 0:
            return None
        
        # Parse output
        info = {}
        for line in stdout.strip().split("\n"):
            if "=" in line:
                key, value = line.split("=", 1)
                info[key.strip()] = value.strip()
        
        try:
            # Parse dates
            not_before = datetime.strptime(
                info.get("notBefore", ""), "%b %d %H:%M:%S %Y %Z"
            )
            not_after = datetime.strptime(
                info.get("notAfter", ""), "%b %d %H:%M:%S %Y %Z"
            )
            
            return Certificate(
                domain=domain,
                issuer=info.get("issuer", "Unknown"),
                valid_from=not_before,
                valid_until=not_after,
                path=cert_path,
                key_path=key_path,
            )
        except (ValueError, KeyError):
            return None
    
    def list_certificates(
        self,
        server: Optional[str] = None,
    ) -> List[Certificate]:
        """List all certificates."""
        certificates = []
        
        # List Let's Encrypt certs
        code, stdout, _ = self._exec(f"ls {self.CERT_DIR} 2>/dev/null", server)
        if code == 0:
            for domain in stdout.strip().split("\n"):
                if domain:
                    cert = self.get_certificate_info(domain, server)
                    if cert:
                        certificates.append(cert)
        
        # List self-signed certs
        code, stdout, _ = self._exec(
            f"ls {self.SELF_SIGNED_DIR}/*.crt 2>/dev/null | xargs -n1 basename | sed 's/.crt$//'",
            server
        )
        if code == 0:
            for domain in stdout.strip().split("\n"):
                if domain and not any(c.domain == domain for c in certificates):
                    cert = self.get_certificate_info(domain, server)
                    if cert:
                        certificates.append(cert)
        
        return certificates
    
    def check_expiring(
        self,
        days: int = 30,
        server: Optional[str] = None,
    ) -> List[Certificate]:
        """Get certificates expiring within specified days."""
        certificates = self.list_certificates(server)
        return [c for c in certificates if c.days_until_expiry <= days]
    
    # =========================================================================
    # Nginx Integration
    # =========================================================================
    
    def nginx_ssl_config(
        self,
        domain: str,
        cert_path: Optional[str] = None,
        key_path: Optional[str] = None,
    ) -> str:
        """
        Generate nginx SSL configuration snippet.
        
        Args:
            domain: Domain name
            cert_path: Certificate path (auto-detected if None)
            key_path: Key path (auto-detected if None)
            
        Returns:
            Nginx configuration string
        """
        if not cert_path:
            cert_path = f"{self.CERT_DIR}/{domain}/fullchain.pem"
        if not key_path:
            key_path = f"{self.CERT_DIR}/{domain}/privkey.pem"
        
        return f"""# SSL Configuration for {domain}
ssl_certificate {cert_path};
ssl_certificate_key {key_path};

ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
ssl_prefer_server_ciphers off;

ssl_session_cache shared:SSL:10m;
ssl_session_timeout 1d;
ssl_session_tickets off;

# OCSP Stapling
ssl_stapling on;
ssl_stapling_verify on;

# Security headers
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
"""
