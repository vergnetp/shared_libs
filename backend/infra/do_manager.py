# backend/infra/do_manager.py
import os
import time
import requests
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

try:
    from .logger import Logger
except ImportError:
    from logger import Logger
try:
    from .health_monitor_installer import HealthMonitorInstaller
except ImportError:
    from health_monitor_installer import HealthMonitorInstaller
try:
    from . import env_loader
except ImportError:
    import env_loader
try:
    from .execute_cmd import CommandExecuter
except ImportError:
    from execute_cmd import CommandExecuter
try:
    from .health_agent_installer import HealthAgentInstaller
except ImportError:
    from health_agent_installer import HealthAgentInstaller
try:
    from .deployment_constants import DIGITALOCEN_API_TOKEN_KEY
except ImportError:
    from deployment_constants import DIGITALOCEN_API_TOKEN_KEY


def log(msg):
    Logger.log(msg)

def get_agent():
    try:
        from .health_monitor import HealthMonitor
    except ImportError:
        from health_monitor import HealthMonitor
    return HealthMonitor

class DOManager:
    """Manage DigitalOcean droplets and resources via API"""
    
    _ssh_key_lock = threading.Lock()
    _vpc_lock = threading.Lock()
    
    # ✓ Per-credential template locks (one template per DO account)
    _template_locks = {}  # credential_hash -> Lock
    _template_locks_lock = threading.Lock()

    # DigitalOcean API base URL
    API_BASE = "https://api.digitalocean.com/v2"

    # Base OS or custom snapshot ID
    # Override with environment variable: DO_BASE_IMAGE
    DROPLET_OS = os.getenv("DO_BASE_IMAGE", "ubuntu-22-04-x64")
    
    # Template configuration
    TEMPLATE_NAME = "deployer-docker-base-template"
    TEMPLATE_SNAPSHOT_PREFIX = "deployer-docker-base"
    TEMPLATE_TAG = "deployer-template"
    
    # Size mapping: (cpu, memory_mb) -> DO size slug
    SIZE_MAP = {
        (1, 1024): "s-1vcpu-1gb",
        (1, 2048): "s-1vcpu-2gb",
        (2, 2048): "s-2vcpu-2gb",
        (2, 4096): "s-2vcpu-4gb",
        (4, 8192): "s-4vcpu-8gb",
        (8, 16384): "s-8vcpu-16gb",
        (16, 32768): "s-16vcpu-32gb",
        (24, 48192): "s-24vcpu-48gb",
        (32, 65536): "s-32vcpu-64gb",
    }
    
    @staticmethod
    def _get_headers(credentials: dict = None) -> Dict[str, str]:
        """Get API request headers with authorization"""
        token = DOManager._get_do_token(credentials)
        if not token:
            raise ValueError(f"{DIGITALOCEN_API_TOKEN_KEY} not found in environment")
        
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    @staticmethod
    def _get_do_token(credentials: dict = None) -> str:
        """
        Get DigitalOcean API token with fallback chain.
        
        Priority:
        1. credentials dict (client-provided token)
        2. .env DIGITALOCEAN_API_TOKEN (your default token)
        
        Args:
            credentials: Credentials dictionary (optional)
        
        Returns:
            DigitalOcean API token
        
        Raises:
            ValueError: If token not found
        """
        # Priority 1: Check credentials dict
        if credentials and credentials.get(DIGITALOCEN_API_TOKEN_KEY):
            return credentials[DIGITALOCEN_API_TOKEN_KEY]
        
        # Priority 2: Check .env
        token = os.getenv(DIGITALOCEN_API_TOKEN_KEY)
        if token:
            return token
        
        raise ValueError("DigitalOcean API token not found in credentials or .env")

    @staticmethod
    def _api_request(method: str, endpoint: str, data: Dict = None, credentials: dict = None) -> Dict:
        """Make API request to DigitalOcean with better error logging"""
        url = f"{DOManager.API_BASE}{endpoint}"
        headers = DOManager._get_headers(credentials)
        
        try:
            if method == "GET":
                response = requests.get(url, headers=headers, timeout=30)
            elif method == "POST":
                response = requests.post(url, headers=headers, json=data, timeout=30)
            elif method == "DELETE":
                response = requests.delete(url, headers=headers, timeout=30)
            elif method == "PUT":
                response = requests.put(url, headers=headers, json=data, timeout=30)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
            
            response.raise_for_status()
            
            # Handle 204 No Content
            if response.status_code == 204:
                return {}
            
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            # ✓ FIXED: Add detailed error logging
            error_msg = f"API request failed: {method} {endpoint} - {e}"
            if hasattr(e.response, 'text'):
                error_msg += f"\nResponse body: {e.response.text}"
            if data:
                error_msg += f"\n\nRequest data: {data}"
            log(error_msg)
            raise
        except requests.exceptions.RequestException as e:
            log(f"API request failed: {method} {endpoint} - {e}")
            raise

    # ========================================
    # TEMPLATE LOCKING (PER-CREDENTIAL)
    # ========================================
    
    @staticmethod
    def _get_template_lock(credentials: dict = None) -> threading.Lock:
        """
        Get or create a lock for this specific credential set.
        Each DigitalOcean account needs its own template and its own lock.
        """
        # Create a stable hash from the credentials
        token = DOManager._get_do_token(credentials)
        credential_hash = hashlib.md5(token.encode()).hexdigest() if token else "default"
        
        with DOManager._template_locks_lock:
            if credential_hash not in DOManager._template_locks:
                DOManager._template_locks[credential_hash] = threading.Lock()
            return DOManager._template_locks[credential_hash]

    # ========================================
    # TEMPLATE & SNAPSHOT MANAGEMENT
    # ========================================
    
    @staticmethod
    def list_snapshots(credentials: dict = None) -> List[Dict[str, Any]]:
        """List all snapshots"""
        response = DOManager._api_request("GET", "/snapshots?resource_type=droplet", credentials=credentials)
        return response.get('snapshots', [])
    
    @staticmethod
    def find_template_snapshot(credentials: dict = None) -> Optional[str]:
        """Find existing template snapshot by name prefix"""
        snapshots = DOManager._list_snapshots(credentials=credentials)
        
        for snapshot in snapshots:
            if snapshot['name'].startswith(DOManager.TEMPLATE_SNAPSHOT_PREFIX):
                log(f"Found template snapshot: {snapshot['name']} (ID: {snapshot['id']})")
                return str(snapshot['id'])
        
        return None
    
    @staticmethod
    def create_snapshot_from_droplet(droplet_id: str, snapshot_name: str = None, credentials: dict = None) -> Optional[str]:
        """
        Create a snapshot from a droplet.
        
        Args:
            droplet_id: Droplet to snapshot
            snapshot_name: Optional custom name
            credentials: optional dict 
            
        Returns:
            Snapshot ID or None if failed
        """
        if snapshot_name is None:            
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            snapshot_name = f"{DOManager.TEMPLATE_SNAPSHOT_PREFIX}-{timestamp}"
        
        log(f"Creating snapshot '{snapshot_name}' from droplet {droplet_id}")
        log("This may take 5-10 minutes...")
        
        try:
            # Power off droplet first (required for snapshot)
            log(f"Powering off droplet {droplet_id}...")
            response = DOManager._api_request("POST", f"/droplets/{droplet_id}/actions", {
                "type": "power_off"
            }, credentials=credentials)
            action_id = response['action']['id']
            
            # Wait for power off
            DOManager._wait_for_action(action_id, timeout=120, credentials=credentials)
            
            # Create snapshot
            log(f"Creating snapshot...")
            response = DOManager._api_request("POST", f"/droplets/{droplet_id}/actions", {
                "type": "snapshot",
                "name": snapshot_name
            }, credentials=credentials)
            action_id = response['action']['id']
            
            # Wait for snapshot completion
            DOManager._wait_for_action(action_id, timeout=600, credentials=credentials)
            
            # Get snapshot ID from droplet's snapshots
            response = DOManager._api_request("GET", f"/droplets/{droplet_id}/snapshots", credentials=credentials)
            snapshots = response.get('snapshots', [])
            
            if not snapshots:
                log("Error: No snapshots found after creation")
                return None
            
            # Find our snapshot by name
            for snapshot in snapshots:
                if snapshot['name'] == snapshot_name:
                    snapshot_id = str(snapshot['id'])
                    log(f"Snapshot created successfully: {snapshot_name} (ID: {snapshot_id})")
                    return snapshot_id
            
            log("Error: Could not find created snapshot")
            return None
            
        except Exception as e:
            log(f"Failed to create snapshot: {e}")
            return None
    
    @staticmethod
    def _wait_for_action(action_id: str, timeout: int = 300, credentials: dict = None) -> bool:
        """Wait for a DigitalOcean action to complete"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = DOManager._api_request("GET", f"/actions/{action_id}", credentials=credentials)
                status = response['action']['status']
                
                if status == "completed":
                    return True
                elif status == "errored":
                    log(f"Action {action_id} failed")
                    return False
                
                time.sleep(5)
            except Exception as e:
                log(f"Error checking action status: {e}")
                time.sleep(5)
        
        log(f"Timeout waiting for action {action_id}")
        return False
    
    @staticmethod
    def delete_snapshot(snapshot_id: str, credentials: dict = None) -> bool:
        """Delete a snapshot"""
        try:
            log(f"Deleting snapshot {snapshot_id}")
            DOManager._api_request("DELETE", f"/snapshots/{snapshot_id}", credentials=credentials)
            log(f"Snapshot {snapshot_id} deleted")
            return True
        except Exception as e:
            log(f"Failed to delete snapshot {snapshot_id}: {e}")
            return False

    @staticmethod
    def _prebake_common_images(server_ip: str):
        """
        Pre-pull commonly used Docker images into template snapshot.
        
        These images will be baked into the snapshot, making deployments faster.
        """
        log("Pre-baking common Docker images into template...")
        
        # List of commonly used images to pre-bake
        common_images = [
            # Databases
            "postgres:15",
            "postgres:14",
            "postgres:13",
            "redis:7-alpine",
            "redis:6-alpine",
            
            # Web servers
            "nginx:alpine",
            "nginx:latest",
            
            # Search
            "opensearchproject/opensearch:2",
            
            # Message queues (optional)
            # "rabbitmq:3-alpine",            
        ]
        
        log(f"Pre-pulling {len(common_images)} images...")
        
        for image in common_images:
            try:
                log(f"  Pulling {image}...")
                CommandExecuter.run_cmd(f"docker pull {image}", server_ip, "root")
                log(f"  ✓ {image}")
            except Exception as e:
                log(f"  ⚠ Failed to pull {image}: {e}")
        
        log("Image pre-baking complete")

    @staticmethod
    def get_or_create_template(region: str, credentials: dict = None) -> str:
        """
        Get or create template snapshot for region.
        
        Template is PER-ACCOUNT (per DO token).
        Uses per-credential locking to prevent parallel creation within same account.
        
        CRITICAL: Lock is held during ENTIRE creation process to prevent race conditions.
        """
        # ✓ FIXED: Check BEFORE acquiring lock (more efficient!)
        snapshot_id = DOManager.find_template_snapshot(credentials=credentials)
        if snapshot_id:
            log(f"Using existing template snapshot: {snapshot_id}")
            return snapshot_id
        
        # Get the lock for THIS credential set
        template_lock = DOManager._get_template_lock(credentials)
        
        with template_lock:
            # ✓ Double-check after acquiring lock (another thread might have created it)
            snapshot_id = DOManager.find_template_snapshot(credentials=credentials)
            if snapshot_id:
                log(f"Using existing template snapshot: {snapshot_id}")
                return snapshot_id
            
            log("No template snapshot found. Creating new template...")
            
            # Create template droplet
            template_id = DOManager._create_raw_droplet(
                name=DOManager.TEMPLATE_NAME,
                region=region,
                cpu=1,
                memory=1024,
                tags=[DOManager.TEMPLATE_TAG],
                use_base_os=True,
                credentials=credentials
            )
            
            if not template_id:
                log("Failed to create template droplet")
                return DOManager.DROPLET_OS
            
            # Wait for droplet to be ready
            DOManager.wait_for_droplet_active(template_id, credentials=credentials)
            info = DOManager.get_droplet_info(template_id, credentials=credentials)
            ip = info['ip']
            
            # Wait for SSH
            DOManager.wait_for_ssh_ready(ip)
            
            # ✓ FIXED: Wait for cloud-init and apt locks, then install pip3
            log("Installing Python pip...")
            CommandExecuter.run_cmd(
                # Wait for cloud-init to complete
                "cloud-init status --wait && "
                # Wait for apt locks to be released (max 5 minutes)
                "timeout 300 bash -c 'while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do echo \"Waiting for apt lock...\"; sleep 2; done' && "
                # Now run apt-get (|| true handles command-not-found errors)
                "apt-get update || true && apt-get install -y python3-pip",
                ip, "root"
            )
            
            # Install Docker
            DOManager.install_docker(ip)
            
            # Install health monitor (no credentials needed in template)
            # Health monitor will get credentials when deployed with actual project
            try:
                HealthMonitorInstaller.install_minimal_on_server(ip)
            except Exception as e:
                log(f"Warning: Health monitor installation had issues: {e}")
                log("Continuing anyway - monitor can be fixed on deployed servers")
            
            # Install health agent
            try:
                HealthAgentInstaller.install_on_server(ip)
            except Exception as e:
                log(f"Warning: Health agent installation had issues: {e}")
                log("Continuing anyway - agent can be fixed on deployed servers")
            
            # WAIT for agent to be ready (optional - not critical)
            log("Waiting for health agent to start...")
            for i in range(10):
                try:
                    response = get_agent().agent_request(ip, "GET", "/ping", timeout=2)
                    if response.get('status') == 'alive':
                        log("✓ Health agent ready")
                        break
                except:
                    time.sleep(1)
            
            log("⚠️  Template SSH remains PUBLIC (will be restricted on deployed servers)")
            
            # Install basic nginx
            DOManager._install_basic_nginx(ip)
            
            DOManager._prebake_common_images(ip)

            log(f"Template droplet {template_id} fully provisioned")
            
            # Create snapshot
            snapshot_id = DOManager.create_snapshot_from_droplet(template_id, credentials=credentials)
            
            if not snapshot_id:
                log("Failed to create snapshot from template")
                DOManager.destroy_droplet(template_id, credentials=credentials)
                return DOManager.DROPLET_OS
            
            # Destroy template droplet (save $6/month)
            log(f"Destroying template droplet {template_id}")
            DOManager.destroy_droplet(template_id, credentials=credentials)
            
            log(f"✅ Template snapshot ready: {snapshot_id}")
            log("   SSH is still public - will be restricted on deployed servers")
            return snapshot_id

    @staticmethod
    def configure_vpc_firewall_on_server(server_ip: str, credentials: dict = None):
        """
        Configure VPC-only firewall on a deployed server.
        
        This should be called AFTER:
        1. Server is provisioned from snapshot
        2. All deployment configuration is complete
        3. We no longer need external SSH access
        
        IMPORTANT: After this, SSH only works from within VPC!
        """
        log(f"🔒 Configuring VPC-only SSH firewall on {server_ip}...")
        
        firewall_commands = [
            "ufw --force reset",
            "ufw default deny incoming",
            "ufw default allow outgoing",
            
            # VPC-only SSH (blocks internet SSH!)
            "ufw allow from 10.0.0.0/16 to any port 22 comment 'SSH VPC only'",
            
            # Agent API (VPC only)
            "ufw allow from 10.0.0.0/16 to any port 9999 comment 'Health Agent'",
            
            # HTTPS (will be restricted to Cloudflare later via agent)
            "ufw allow 443 comment 'HTTPS - will be restricted in deployment'",
            
            "ufw --force enable"
        ]
        
        for cmd in firewall_commands:
            try:
                CommandExecuter.run_cmd(cmd, server_ip, "root")
                log(f"  ✓ {cmd[:60]}...")
            except Exception as e:
                log(f"  ⚠ Firewall command failed: {e}")
        
        log("✅ Firewall configured - SSH restricted to VPC (10.0.0.0/16)")
        log("   ⚠️  From now on, SSH only works from within VPC!")

    @staticmethod
    def delete_template(credentials: dict = None):
        """
        Delete template snapshot and any template droplets.
        Use this when you want to rebuild the template from scratch.
        """
        # ✓ FIXED: Use per-credential lock
        template_lock = DOManager._get_template_lock(credentials)
        
        with template_lock:
            log("Deleting template resources...")
            
            # Delete template droplets
            droplets = DOManager.list_droplets(tags=[DOManager.TEMPLATE_TAG], credentials=credentials)
            for droplet in droplets:
                DOManager.destroy_droplet(droplet['droplet_id'], credentials=credentials)
            
            # Delete template snapshots
            snapshots = DOManager.list_snapshots(credentials=credentials)
            for snapshot in snapshots:
                if snapshot['name'].startswith(DOManager.TEMPLATE_SNAPSHOT_PREFIX):
                    DOManager.delete_snapshot(str(snapshot['id']), credentials=credentials)
            
            log("Template resources deleted")

    # ========================================
    # SSH KEY MANAGEMENT
    # ========================================
    
    @staticmethod
    def get_or_create_ssh_key(credentials: Dict=None) -> str:
        """Get or create SSH key for droplet access (thread-safe)"""
        with DOManager._ssh_key_lock:
            ssh_key_name = "deployer_key"
            
            # Read public key first
            public_key_path = Path.home() / ".ssh" / "deployer_id_rsa.pub"
            if not public_key_path.exists():
                raise FileNotFoundError(f"SSH public key not found: {public_key_path}")
            
            public_key = public_key_path.read_text().strip()
            
            # Check if key exists
            response = DOManager._api_request("GET", "/account/keys", credentials=credentials)
            for key in response.get('ssh_keys', []):
                if key['name'] == ssh_key_name:
                    log(f"Using existing SSH key: {ssh_key_name}")
                    return str(key['id'])
                # Also check by fingerprint (in case name changed)
                if key.get('public_key') == public_key:
                    log(f"Using existing SSH key by fingerprint: {key['name']}")
                    return str(key['id'])
            
            # Create new key only if it doesn't exist
            try:
                response = DOManager._api_request("POST", "/account/keys", {
                    "name": ssh_key_name,
                    "public_key": public_key
                }, credentials=credentials)
                log(f"Created new SSH key: {ssh_key_name}")
                return str(response['ssh_key']['id'])
            except Exception as e:
                # If creation fails, try to find it again (race condition)
                if "422" in str(e) or "Unprocessable Entity" in str(e):
                    log("SSH key creation returned 422, checking if it exists now...")
                    response = DOManager._api_request("GET", "/account/keys", credentials=credentials)
                    for key in response.get('ssh_keys', []):
                        if key['name'] == ssh_key_name or key.get('public_key') == public_key:
                            log(f"Found SSH key after 422: {key['name']}")
                            return str(key['id'])
                raise

    # ========================================
    # SIZE MANAGEMENT
    # ========================================
    
    @staticmethod
    def specs_to_size(cpu: int, memory: int) -> str:
        """Convert CPU/memory specs to DigitalOcean size slug"""
        key = (cpu, memory)
        if key not in DOManager.SIZE_MAP:
            raise ValueError(f"Unsupported size: {cpu} CPU, {memory}MB RAM")
        return DOManager.SIZE_MAP[key]
    
    @staticmethod
    def size_to_specs(size_slug: str) -> Tuple[int, int]:
        """Convert size slug to (cpu, memory) specs"""
        for (cpu, memory), slug in DOManager.SIZE_MAP.items():
            if slug == size_slug:
                return cpu, memory
        
        # Fallback parsing from slug format (e.g., "s-2vcpu-4gb")
        parts = size_slug.split('-')
        try:
            cpu = int(parts[1].replace('vcpu', ''))
            memory_gb = int(parts[2].replace('gb', ''))
            return cpu, memory_gb * 1024
        except:
            return 1, 1024  # Default

    # ========================================
    # VPC MANAGEMENT
    # ========================================
    
    @staticmethod
    def list_vpcs(credentials: Dict=None) -> List[Dict[str, Any]]:
        """List all VPCs in DO account"""
        response = DOManager._api_request("GET", "/vpcs", credentials=credentials)
        return response.get("vpcs", [])
    
    @staticmethod
    def get_or_create_vpc(region: str, ip_range: str = "10.0.0.0/16", credentials:Dict=None) -> str:
        """Thread-safe VPC creation - VPC is USER-SPECIFIC (per DO account)"""
        vpc_name = f"deployer-vpc-{region}"
        
        with DOManager._vpc_lock:
            # ✓ FIXED: Check existing VPCs IN THIS USER'S ACCOUNT
            vpcs = DOManager.list_vpcs(credentials=credentials)
            existing = [v for v in vpcs if v['name'] == vpc_name and v['region'] == region]
            
            if existing:
                vpc_id = existing[0]['id']
                log(f"Using existing VPC '{vpc_name}' (ID: {vpc_id})")
                return vpc_id
            
            # Create new VPC
            log(f"Creating VPC '{vpc_name}' in region {region}")
            response = DOManager._api_request("POST", "/vpcs", {
                "name": vpc_name,
                "region": region,
                "ip_range": ip_range
            }, credentials=credentials)
            
            vpc_id = response['vpc']['id']
            log(f"VPC created successfully (ID: {vpc_id})")
            return vpc_id

    # ========================================
    # DROPLET LIFECYCLE
    # ========================================
    
    @staticmethod
    def _create_raw_droplet(
        name: str,
        region: str,
        cpu: int,
        memory: int,
        tags: List[str] = None,
        use_base_os: bool = False,
        credentials: Dict=None
    ) -> Optional[str]:
        """
        Create a raw droplet via API (no provisioning).
        Internal method - use create_server() for normal deployments.
        
        Args:
            use_base_os: If True, use base Ubuntu instead of template snapshot
        """
        # Get prerequisites
        ssh_key_id = DOManager.get_or_create_ssh_key(credentials=credentials)
        vpc_uuid = DOManager.get_or_create_vpc(region, credentials=credentials)
        size = DOManager.specs_to_size(cpu, memory)
        
        # Determine image to use
        if use_base_os:
            image = "ubuntu-22-04-x64"
        else:
            image = DOManager.DROPLET_OS
        
        # Prepare droplet configuration
        droplet_config = {
            "name": name,
            "region": region,
            "size": size,
            "image": image,
            "ssh_keys": [ssh_key_id],
            "vpc_uuid": vpc_uuid,
            "tags": tags or []
        }
        
        log(f"Creating droplet '{name}' in {region} ({cpu} CPU, {memory}MB RAM)")
        response = DOManager._api_request("POST", "/droplets", droplet_config, credentials=credentials)
        
        droplet_id = str(response['droplet']['id'])
        log(f"Droplet creation initiated (ID: {droplet_id})")
        
        return droplet_id
    
    @staticmethod
    def create_droplet(
        name: str,
        region: str,
        cpu: int,
        memory: int,
        tags: List[str] = None,
        credentials: Dict=None
    ) -> str:
        """
        Create a single droplet and provision it completely.
        LEGACY METHOD - Use for creating template droplets.
        For production servers, use create_server() which uses pre-baked snapshots.
        
        Process:
        1. Create droplet via API
        2. Wait for active
        3. Wait for SSH
        4. Install Docker
        5. Install health monitor
        6. Install nginx
        
        Returns:
            droplet_id (str)
        """
        droplet_id = DOManager._create_raw_droplet(name, region, cpu, memory, tags, use_base_os=True, credentials=credentials)
        
        if not droplet_id:
            raise Exception("Failed to create droplet")
        
        # Wait for droplet to become active
        DOManager.wait_for_droplet_active(droplet_id, credentials=credentials)
        
        # Get droplet info (includes IP)
        info = DOManager.get_droplet_info(droplet_id, credentials=credentials)
        ip = info['ip']
        
        # Wait for SSH
        DOManager.wait_for_ssh_ready(ip, credentials=credentials)
        
        # Install Docker
        DOManager.install_docker(ip, credentials=credentials)
        
        # Install health monitor        
        HealthMonitorInstaller.install_on_server(ip, credentials=credentials)
        
        # Install basic nginx
        DOManager._install_basic_nginx(ip, credentials=credentials)

        log(f"Droplet {droplet_id} ({ip}) fully provisioned")
        
        return droplet_id
    
    @staticmethod
    def create_server(
        name: str,
        region: str,
        cpu: int,
        memory: int,
        tags: List[str] = None,
        credentials:Dict=None
    ) -> str:
        """
        Create a server from pre-baked template snapshot (FAST).
        Use this for all production server creation.
        
        Process:
        1. Ensure template snapshot exists
        2. Create droplet from snapshot
        3. Wait for active
        4. Wait for SSH
        5. Done! (Docker, nginx, etc. already installed)
        
        Returns:
            droplet_id (str)
        """
        # Ensure we have a template snapshot
        snapshot_id = DOManager.get_or_create_template(region, credentials=credentials)
        
        # Temporarily override DROPLET_OS to use snapshot
        original_os = DOManager.DROPLET_OS
        DOManager.DROPLET_OS = snapshot_id
        
        try:
            # Create droplet from snapshot
            droplet_id = DOManager._create_raw_droplet(name, region, cpu, memory, tags, credentials=credentials)
            
            if not droplet_id:
                raise Exception("Failed to create server")
            
            # Wait for droplet to become active
            DOManager.wait_for_droplet_active(droplet_id, credentials=credentials)
            
            # Get droplet info
            info = DOManager.get_droplet_info(droplet_id, credentials=credentials)
            ip = info['ip']
            
            # Wait for SSH (much faster with snapshot!)
            DOManager.wait_for_ssh_ready(ip)
            
            log(f"Server {droplet_id} ({ip}) ready (from template snapshot)")
            
            return droplet_id
            
        finally:
            # Restore original OS setting
            DOManager.DROPLET_OS = original_os
    
    @staticmethod
    def _install_basic_nginx(server_ip: str):
        """Install nginx container with empty config directories"""

        # Create nginx directories
        CommandExecuter.run_cmd("mkdir -p /etc/nginx/conf.d /etc/nginx/stream.d", server_ip, "root")
        
        # Create basic nginx.conf with stream support
        nginx_conf = """events { worker_connections 1024; }
stream { include /etc/nginx/stream.d/*.conf; }
http { include /etc/nginx/conf.d/*.conf; }
"""
        
        CommandExecuter.run_cmd_with_stdin(
            "cat > /etc/nginx/nginx.conf",
            nginx_conf.encode('utf-8'),
            server_ip, "root"
        )
        
        # DON'T START NGINX IN THE TEMPLATE!
        # Just create the config files. Nginx will be started by deployer
        # with the correct project-specific network.
        
        log(f"Nginx config directories prepared on {server_ip}")

    @staticmethod
    def create_servers(
        count: int,
        region: str,
        cpu: int,
        memory: int,
        tags: List[str] = None,
        credentials: Dict=None
    ) -> List[Dict[str, Any]]:
        """
        Create multiple servers in parallel using template snapshot.
        
        Returns:
            List of dicts: [{droplet_id, ip, private_ip, zone, cpu, memory, created}, ...]
        """
        log(f"Creating {count} servers in {region} (parallel execution)")
        Logger.start()
        
        droplet_ids = []
        
        # Create servers in parallel
        with ThreadPoolExecutor(max_workers=min(count, 10)) as executor:
            futures = []
            
            for i in range(count):
                name = f"server-{region}-{int(time.time())}-{i}"
                future = executor.submit(
                    DOManager.create_server,
                    name, region, cpu, memory, tags, credentials=credentials
                )
                futures.append(future)
            
            # Collect results as they complete
            for future in as_completed(futures):
                try:
                    droplet_id = future.result()
                    droplet_ids.append(droplet_id)
                    log(f"Progress: {len(droplet_ids)}/{count} servers ready")
                except Exception as e:
                    log(f"Failed to create server: {e}")
        
        # Gather all server info
        servers_info = []
        for droplet_id in droplet_ids:
            info = DOManager.get_droplet_info(droplet_id, credentials=credentials)
            servers_info.append(info)
        
        Logger.end()
        log(f"Successfully created {len(servers_info)}/{count} servers")
        
        return servers_info
    
    @staticmethod
    def create_droplets(
        count: int,
        region: str,
        cpu: int,
        memory: int,
        tags: List[str] = None,
        credentials: Dict=None
    ) -> List[Dict[str, Any]]:
        """
        LEGACY: Create multiple droplets in parallel (slow, full provisioning).
        Use create_servers() instead for production.
        
        Returns:
            List of dicts: [{droplet_id, ip, private_ip, zone, cpu, memory, created}, ...]
        """
        log(f"Creating {count} droplets in {region} (parallel execution, LEGACY MODE)")
        Logger.start()
        
        droplet_ids = []
        
        # Create droplets in parallel
        with ThreadPoolExecutor(max_workers=min(count, 10)) as executor:
            futures = []
            
            for i in range(count):
                name = f"droplet-{region}-{int(time.time())}-{i}"
                future = executor.submit(
                    DOManager.create_droplet,
                    name, region, cpu, memory, tags, credentials=credentials
                )
                futures.append(future)
            
            # Collect results as they complete
            for future in as_completed(futures):
                try:
                    droplet_id = future.result()
                    droplet_ids.append(droplet_id)
                    log(f"Progress: {len(droplet_ids)}/{count} droplets ready")
                except Exception as e:
                    log(f"Failed to create droplet: {e}")
        
        # Gather all droplet info
        droplets_info = []
        for droplet_id in droplet_ids:
            info = DOManager.get_droplet_info(droplet_id, credentials=credentials)
            droplets_info.append(info)
        
        Logger.end()
        log(f"Successfully created {len(droplets_info)}/{count} droplets")
        
        return droplets_info
    
    @staticmethod
    def destroy_droplet(droplet_id: str, credentials: Dict=None) -> bool:
        """Destroy a droplet"""
        try:
            log(f"Destroying droplet {droplet_id}")
            DOManager._api_request("DELETE", f"/droplets/{droplet_id}", credentials=credentials)
            log(f"Droplet {droplet_id} destroyed")
            return True
        except Exception as e:
            log(f"Failed to destroy droplet {droplet_id}: {e}")
            return False
    
    @staticmethod
    def wait_for_droplet_active(droplet_id: str, timeout: int = 180, credentials: Dict=None) -> bool:
        """Wait for droplet to reach 'active' status"""
        log(f"Waiting for droplet {droplet_id} to become active...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            info = DOManager.get_droplet_info(droplet_id, credentials=credentials)
            status = info['status']
            
            if status == 'active':
                log(f"Droplet {droplet_id} is active")
                return True
            
            time.sleep(5)
        
        log(f"Timeout waiting for droplet {droplet_id} to become active")
        return False
    
    @staticmethod
    def wait_for_ssh_ready(ip: str, timeout: int = 60) -> bool:
        """Wait for SSH to be available on droplet"""
        log(f"Waiting for SSH on {ip}...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                CommandExecuter.run_cmd("echo 'ready'", ip, "root")
                log(f"SSH ready on {ip}")
                return True
            except Exception:
                pass
            
            time.sleep(5)
        
        log(f"Timeout waiting for SSH on {ip}")
        return False

    # ========================================
    # DROPLET CONFIGURATION
    # ========================================
    
    @staticmethod
    def install_docker(ip: str, user: str = "root") -> bool:
        """SSH to droplet and install Docker using official convenience script"""
        log(f"Installing Docker on {ip}...")
        
        try:
            # Wait for cloud-init to complete
            log(f"Waiting for cloud-init to complete on {ip}...")
            
            for attempt in range(3):
                try:
                    CommandExecuter.run_cmd(
                        "cloud-init status --wait || timeout 300 bash -c 'while [ ! -f /var/lib/cloud/instance/boot-finished ]; do sleep 5; done'",
                        ip, user
                    )
                    break
                except Exception as e:
                    if attempt == 2:
                        raise
                    log(f"Cloud-init wait attempt {attempt + 1} failed, retrying...")
                    time.sleep(10)
            
            # Install Docker
            commands = [
                "curl -fsSL https://get.docker.com -o get-docker.sh",
                "sh get-docker.sh",
                "rm get-docker.sh",
                "systemctl start docker",
                "systemctl enable docker",
                "timeout 30 bash -c 'until docker ps >/dev/null 2>&1; do echo \"Waiting for Docker daemon...\"; sleep 2; done'",
                "docker --version",
                "docker ps"
            ]
            
            for cmd in commands:
                CommandExecuter.run_cmd(cmd, ip, user)
            
            log(f"Docker installed and verified on {ip}")
            return True
            
        except Exception as e:
            log(f"Failed to install Docker on {ip}: {e}")
            return False

    # ========================================
    # DROPLET QUERY
    # ========================================
    
    @staticmethod
    def get_droplet_info(droplet_id: str, credentials: Dict=None) -> Dict[str, Any]:
        """Get droplet details"""
        response = DOManager._api_request("GET", f"/droplets/{droplet_id}", credentials=credentials)
        droplet = response['droplet']
        
        # Extract IPs
        public_ip = None
        private_ip = None
        
        for network in droplet.get('networks', {}).get('v4', []):
            if network['type'] == 'public':
                public_ip = network['ip_address']
            elif network['type'] == 'private':
                private_ip = network['ip_address']
        
        # Parse size to get specs
        size_slug = droplet['size']['slug']
        cpu, memory = DOManager.size_to_specs(size_slug)
        
        return {
            'droplet_id': str(droplet['id']),
            'name': droplet['name'],
            'ip': public_ip,
            'private_ip': private_ip,
            'zone': droplet['region']['slug'],
            'size': size_slug,
            'status': droplet['status'],
            'created': droplet['created_at'],
            'tags': droplet.get('tags', []),
            'cpu': cpu,
            'memory': memory
        }

    @staticmethod
    def list_droplets(tags: List[str] = None, credentials: dict = None) -> List[Dict[str, Any]]:
        """List all droplets, optionally filtered by tags"""
        
        # Default to only listing "Infra" tagged droplets
        if tags is None:
            tags = ["Infra"]
        
        endpoint = "/droplets"
        
        if tags:
            tag_query = "&".join([f"tag_name={tag}" for tag in tags])
            endpoint = f"/droplets?{tag_query}"
        
        response = DOManager._api_request("GET", endpoint, credentials=credentials)
        
        droplets = []
        for d in response.get('droplets', []):
            droplets.append(DOManager.get_droplet_info(str(d['id']), credentials=credentials))
        
        return droplets
    
    @staticmethod
    def update_droplet_tags(droplet_id: int, add_tags: List[str] = None, remove_tags: List[str] = None, credentials: dict = None):
        """Update tags for a droplet using DigitalOcean's tag resource API"""
        try:
            droplet_id_str = str(droplet_id)
            
            # Remove old tags
            if remove_tags:
                for tag in remove_tags:
                    try:
                        resource_data = {
                            "resources": [{
                                "resource_id": droplet_id_str,
                                "resource_type": "droplet"
                            }]
                        }
                        
                        url = f"{DOManager.API_BASE}/tags/{tag}/resources"
                        headers = DOManager._get_headers(credentials)
                        response = requests.delete(url, headers=headers, json=resource_data, timeout=30)
                        
                        if response.status_code in [204, 200]:
                            log(f"Removed tag '{tag}' from droplet {droplet_id}")
                    except Exception as e:
                        log(f"Warning: Could not remove tag '{tag}': {e}")
            
            # Add new tags
            if add_tags:
                for tag in add_tags:
                    try:
                        # Ensure tag exists
                        DOManager._api_request("POST", "/tags", {"name": tag}, credentials=credentials)
                    except:
                        pass  # Tag might already exist
                    
                    try:
                        resource_data = {
                            "resources": [{
                                "resource_id": droplet_id_str,
                                "resource_type": "droplet"
                            }]
                        }
                        
                        url = f"{DOManager.API_BASE}/tags/{tag}/resources"
                        headers = DOManager._get_headers(credentials)
                        response = requests.post(url, headers=headers, json=resource_data, timeout=30)
                        
                        if response.status_code in [201, 204]:
                            log(f"Added tag '{tag}' to droplet {droplet_id}")
                    except Exception as e:
                        log(f"Warning: Could not add tag '{tag}': {e}")
                        
        except Exception as e:
            log(f"Failed to update droplet tags: {e}")
            raise
    
    # ========================================
    # HELPER ALIAS FOR COMPATIBILITY
    # ========================================
    
    @staticmethod
    def _list_snapshots(credentials: dict = None) -> List[Dict[str, Any]]:
        """Alias for list_snapshots"""
        return DOManager.list_snapshots(credentials=credentials)