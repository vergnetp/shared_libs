# backend/infra/certificate_manager.py

import os
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import subprocess

from nginx_config_generator import NginxConfigGenerator
from logger import Logger

def log(msg):
    Logger.log(msg)

class CertificateManager:
    """
    Manages SSL certificate lifecycle: checking expiry and triggering renewals.
    
    Integrates with NginxConfigGenerator for actual certificate operations.
    Used by health monitor for automatic renewal on each server.
    """
    
    # Certificate renewal settings
    RENEWAL_THRESHOLD_DAYS = 30  # Renew when < 30 days until expiry
    
    @staticmethod
    def check_expiry(domain: str) -> Optional[int]:
        """
        Check how many days until certificate expires.
        
        Args:
            domain: Domain name to check
            
        Returns:
            Days until expiry, or None if certificate not found
        """
        cert_path = f"/local/nginx/certs/letsencrypt/live/{domain}/cert.pem"
        
        if not os.path.exists(cert_path):
            return None
        
        try:
            # Use openssl to check expiry date
            result = subprocess.run(
                ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode != 0:
                return None
            
            # Parse output: "notAfter=Jan 20 12:00:00 2025 GMT"
            expiry_str = result.stdout.strip().replace("notAfter=", "")
            expiry_date = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z")
            
            # Calculate days until expiry
            days_remaining = (expiry_date - datetime.now()).days
            
            return days_remaining
            
        except Exception as e:
            log(f"Error checking certificate expiry for {domain}: {e}")
            return None
    
    @staticmethod
    def get_domains_from_nginx_configs() -> List[Dict[str, str]]:
        """
        Parse nginx config files on THIS server to extract domains.
        
        Returns:
            List of dicts: [
                {'domain': 'example.com', 'project': 'myapp', 'env': 'prod', 'service': 'api'},
                ...
            ]
        """
        domains = []
        nginx_config_dir = Path("/local/nginx/configs")
        
        if not nginx_config_dir.exists():
            return domains
        
        try:
            # Iterate through all nginx config files on THIS server
            for config_file in nginx_config_dir.glob("**/*.conf"):
                # Parse filename: {project}_{env}_{service}.conf
                filename = config_file.stem
                parts = filename.split('_')
                
                if len(parts) < 3:
                    continue
                
                project = parts[0]
                env = parts[1]
                service = '_'.join(parts[2:])  # Handle service names with underscores
                
                # Read config file and extract server_name
                with open(config_file, 'r') as f:
                    content = f.read()
                    
                    # Find server_name directive
                    for line in content.split('\n'):
                        line = line.strip()
                        if line.startswith('server_name ') and not line.startswith('server_name _'):
                            # Extract domain: "server_name example.com;"
                            domain = line.replace('server_name ', '').replace(';', '').strip()
                            
                            domains.append({
                                'domain': domain,
                                'project': project,
                                'env': env,
                                'service': service
                            })
                            break
        
        except Exception as e:
            log(f"Error parsing nginx configs: {e}")
        
        return domains
    
    @staticmethod
    def check_and_renew_all() -> Dict[str, bool]:
        """
        Check all certificates on THIS server and renew those expiring soon.
        
        Uses existing NginxConfigGenerator._renew_certificates() for actual renewal.
        Operates entirely on local files - no SSH to other servers.
        
        Returns:
            Dict mapping domain to renewal result:
            {
                'example.com': True,   # Renewed successfully
                'another.com': False,  # Renewal failed
                'valid.com': None      # No renewal needed (still valid)
            }
        """       
        log("🔒 Checking SSL certificates for renewal...")
        
        results = {}
        
        # Parse nginx configs on THIS server (local file system)
        domains_info = CertificateManager.get_domains_from_nginx_configs()
        
        if not domains_info:
            log("No domains found in nginx configs")
            return results
        
        # Get email and cloudflare token from environment
        email = os.getenv('CLOUDFLARE_EMAIL') or os.getenv('ADMIN_EMAIL')
        cloudflare_api_token = os.getenv('CLOUDFLARE_API_TOKEN')
        
        if not email:
            log("⚠️  No email configured for certificate renewal (set CLOUDFLARE_EMAIL or ADMIN_EMAIL)")
            return results
        
        # Check each certificate on THIS server
        for domain_info in domains_info:
            domain = domain_info['domain']
            project = domain_info['project']
            env = domain_info['env']
            service = domain_info['service']
            
            try:
                # Check expiry locally (reads local cert file)
                days_remaining = CertificateManager.check_expiry(domain)
                
                if days_remaining is None:
                    log(f"⚠️  No certificate found for {domain} ({project}/{env}/{service})")
                    log(f"   Issuing new certificate...")
                    
                    # Issue new certificate LOCALLY (no SSH)
                    success = NginxConfigGenerator._renew_certificates(
                        target_server="localhost",  # Run certbot on THIS server
                        project=project,
                        env=env,
                        service=service,
                        email=email,
                        cloudflare_api_token=cloudflare_api_token
                    )
                    
                    results[domain] = success
                    
                elif days_remaining < CertificateManager.RENEWAL_THRESHOLD_DAYS:
                    log(f"⚠️  Certificate for {domain} expires in {days_remaining} days ({project}/{env}/{service})")
                    log(f"   Renewing certificate...")
                    
                    # Renew certificate LOCALLY (no SSH)
                    success = NginxConfigGenerator._renew_certificates(
                        target_server="localhost",  # Run certbot on THIS server
                        project=project,
                        env=env,
                        service=service,
                        email=email,
                        cloudflare_api_token=cloudflare_api_token
                    )
                    
                    results[domain] = success
                    
                    if success:
                        log(f"✓ Successfully renewed certificate for {domain}")
                    else:
                        log(f"❌ Failed to renew certificate for {domain}")
                    
                else:
                    log(f"✓ Certificate for {domain} is valid ({days_remaining} days remaining)")
                    results[domain] = None  # No renewal needed
                    
            except Exception as e:
                log(f"Error processing certificate for {domain}: {e}")
                results[domain] = False
        
        return results