import os
import time
import requests
import subprocess
from typing import List, Dict, Any, Tuple, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from logger import Logger
from execute_cmd import CommandExecuter
import threading
import env_loader

def log(msg):
    Logger.log(msg)


class DOManager:
    """Manage DigitalOcean droplets and resources via API"""
    
    _ssh_key_lock = threading.Lock()
    _vpc_lock = threading.Lock()

    # DigitalOcean API base URL
    API_BASE = "https://api.digitalocean.com/v2"

    DROPLET_OS = "ubuntu-22-04-x64"  # Ubuntu 22.04 LTS
    
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
    def _get_headers() -> Dict[str, str]:
        """Get API request headers with authorization"""
        token = os.getenv("DIGITALOCEAN_API_TOKEN")
        if not token:
            raise ValueError("DIGITALOCEAN_API_TOKEN not found in environment")
        
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    @staticmethod
    def _api_request(method: str, endpoint: str, data: Dict = None) -> Dict:
        """Make API request to DigitalOcean"""
        url = f"{DOManager.API_BASE}{endpoint}"
        headers = DOManager._get_headers()
        
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
            
            # DELETE requests may not return JSON
            if method == "DELETE":
                return {"success": True}
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            log(f"DigitalOcean API error: {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    log(f"Error details: {error_detail}")
                except:
                    log(f"Response text: {e.response.text}")
            raise
    
    # ========================================
    # UTILITY / HELPERS
    # ========================================
    
    @staticmethod
    def check_api_token() -> bool:
        """Verify DIGITALOCEAN_API_TOKEN is valid"""
        try:
            DOManager._api_request("GET", "/account")
            return True
        except Exception as e:
            log(f"Invalid DigitalOcean API token: {e}")
            return False
    
    @staticmethod
    def specs_to_size(cpu: int, memory: int) -> str:
        """
        Convert CPU/memory specs to DO size slug.
        
        Examples:
            (2, 4096) -> "s-2vcpu-4gb"
            (4, 8192) -> "s-4vcpu-8gb"
        """
        size = DOManager.SIZE_MAP.get((cpu, memory))
        if not size:
            raise ValueError(
                f"No DO size found for specs: {cpu} CPU, {memory}MB RAM. "
                f"Available: {list(DOManager.SIZE_MAP.keys())}"
            )
        return size
    
    @staticmethod
    def size_to_specs(size: str) -> Tuple[int, int]:
        """
        Parse DO size slug to CPU/memory specs.
        
        Examples:
            "s-2vcpu-4gb" -> (2, 4096)
            "s-4vcpu-8gb" -> (4, 8192)
        """
        for (cpu, memory), slug in DOManager.SIZE_MAP.items():
            if slug == size:
                return (cpu, memory)
        
        # Try to parse if not in map (e.g., "s-2vcpu-4gb")
        try:
            parts = size.split('-')
            cpu_part = [p for p in parts if 'vcpu' in p][0]
            mem_part = [p for p in parts if 'gb' in p][0]
            
            cpu = int(cpu_part.replace('vcpu', ''))
            memory_gb = int(mem_part.replace('gb', ''))
            memory = memory_gb * 1024
            
            return (cpu, memory)
        except Exception:
            raise ValueError(f"Cannot parse size slug: {size}")
    
    # ========================================
    # SSH KEY MANAGEMENT
    # ========================================
    
    @staticmethod
    def list_ssh_keys() -> List[Dict[str, Any]]:
        """List all SSH keys in DO account"""
        response = DOManager._api_request("GET", "/account/keys")
        return response.get("ssh_keys", [])
    
    @staticmethod
    def get_or_create_ssh_key(name: str = "deployer_key") -> int:
        """
        Get or create SSH key for deployments (cross-platform via Docker).
        Thread-safe for parallel droplet creation.
        
        Process:
        1. Check if local key exists (~/.ssh/deployer_id_rsa)
        2. If not, generate using Docker container (works on Windows/Linux/macOS)
        3. Check if public key uploaded to DO (by name)
        4. If not, upload public key to DO
        
        Returns:
            ssh_key_id (int)
        """
        with DOManager._ssh_key_lock:  # Thread-safe key generation
            import platform
            
            local_key_path = Path.home() / ".ssh" / "deployer_id_rsa"
            public_key_path = local_key_path.with_suffix(".pub")
            
            # Generate locally if missing
            if not local_key_path.exists():
                log(f"Generating SSH key pair at {local_key_path}")
                local_key_path.parent.mkdir(parents=True, exist_ok=True)
                
                system = platform.system()
                
                # Convert Windows path to WSL/Docker-compatible format
                if system == "Windows":
                    ssh_dir = str(local_key_path.parent).replace("\\", "/")
                    # Convert C:/ to /c/ for Docker volume mount
                    if ssh_dir[1] == ":":
                        ssh_dir = f"/{ssh_dir[0].lower()}{ssh_dir[2:]}"
                else:
                    ssh_dir = str(local_key_path.parent)
                
                # Use Docker to generate SSH key (works everywhere)
                docker_cmd = [
                    "docker", "run", "--rm",
                    "-v", f"{ssh_dir}:/root/.ssh",
                    "alpine:latest",
                    "sh", "-c",
                    "apk add --no-cache openssh-keygen && "
                    "ssh-keygen -t rsa -b 4096 -f /root/.ssh/deployer_id_rsa -N '' -C 'deployer@automated'"
                ]
                
                log("Using Docker to generate SSH key...")
                subprocess.run(docker_cmd, check=True)
                
                # Set proper permissions (only on Unix)
                if system != "Windows":
                    local_key_path.chmod(0o600)
                    public_key_path.chmod(0o644)
            
            # Read public key
            if not public_key_path.exists():
                raise FileNotFoundError(
                    f"Public key not found at {public_key_path}\n"
                    "SSH key generation failed"
                )
            
            public_key = public_key_path.read_text().strip()
        
        # Check if already in DO (outside lock - API calls are thread-safe)
        do_keys = DOManager.list_ssh_keys()
        existing = [k for k in do_keys if k['name'] == name]
        
        if existing:
            log(f"Using existing SSH key '{name}' (ID: {existing[0]['id']})")
            return existing[0]['id']
        
        # Upload to DO
        log(f"Uploading SSH key '{name}' to DigitalOcean")
        response = DOManager._api_request("POST", "/account/keys", {
            "name": name,
            "public_key": public_key
        })
        
        key_id = response['ssh_key']['id']
        log(f"SSH key uploaded successfully (ID: {key_id})")
        return key_id

    # ========================================
    # VPC MANAGEMENT
    # ========================================
    
    @staticmethod
    def list_vpcs() -> List[Dict[str, Any]]:
        """List all VPCs in DO account"""
        if os.getenv("DIGITALOCEAN_API_TOKEN") is None:
            return []
        response = DOManager._api_request("GET", "/vpcs")
        return response.get("vpcs", [])
    
    @staticmethod
    def get_or_create_vpc(region: str, ip_range: str = "10.0.0.0/16") -> str:
        """Thread-safe VPC creation"""
        vpc_name = f"deployer-vpc-{region}"
        
        with DOManager._vpc_lock:  # Protect creation
            # Check existing VPCs
            vpcs = DOManager.list_vpcs()
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
            })
            
            vpc_id = response['vpc']['id']
            log(f"VPC created successfully (ID: {vpc_id})")
            return vpc_id
    
    # ========================================
    # DROPLET LIFECYCLE
    # ========================================
    
    @staticmethod
    def create_droplet(
        name: str,
        region: str,
        cpu: int,
        memory: int,
        tags: List[str] = None
    ) -> str:
        """
        Create a single droplet and set it up completely.
        
        Process:
        1. Create droplet via API
        2. Wait for active
        3. Wait for SSH
        4. Install Docker
        5. Install health monitor
        
        Returns:
            droplet_id (str)
        """
        # Get prerequisites
        ssh_key_id = DOManager.get_or_create_ssh_key()
        vpc_uuid = DOManager.get_or_create_vpc(region)
        size = DOManager.specs_to_size(cpu, memory)
        
        # Prepare droplet configuration
        droplet_config = {
            "name": name,
            "region": region,
            "size": size,
            "image": DOManager.DROPLET_OS,
            "ssh_keys": [ssh_key_id],
            "vpc_uuid": vpc_uuid,
            "tags": tags or []
        }
        
        log(f"Creating droplet '{name}' in {region} ({cpu} CPU, {memory}MB RAM)")
        response = DOManager._api_request("POST", "/droplets", droplet_config)
        
        droplet_id = str(response['droplet']['id'])
        log(f"Droplet creation initiated (ID: {droplet_id})")
        
        # Wait for droplet to become active
        DOManager.wait_for_droplet_active(droplet_id)
        
        # Get droplet info (includes IP)
        info = DOManager.get_droplet_info(droplet_id)
        ip = info['ip']
        
        # Wait for SSH
        DOManager.wait_for_ssh_ready(ip)
        
        # Install Docker
        DOManager.install_docker(ip)
        
        # Install health monitor
        from health_monitor_installer import HealthMonitorInstaller
        HealthMonitorInstaller.install_on_server(ip)        

        # Create a minimal project/env context for nginx
        # Or install nginx without project context
        DOManager._install_basic_nginx(ip)

        log(f"Droplet {droplet_id} ({ip}) fully provisioned")
        
        return droplet_id
    
    def _install_basic_nginx(server_ip: str):
        """Install nginx container with empty config directories"""
        from execute_docker import DockerExecuter
        from execute_cmd import CommandExecuter
        
        # Create nginx directories
        CommandExecuter.run_cmd("mkdir -p /local/nginx/conf.d /local/nginx/stream.d", server_ip, "root")
        
        # Create basic nginx.conf with stream support
        nginx_conf = """
    events { worker_connections 1024; }
    stream { include /etc/nginx/stream.d/*.conf; }
    http { include /etc/nginx/conf.d/*.conf; }
    """
        
        CommandExecuter.run_cmd_with_stdin(
            "cat > /local/nginx/nginx.conf",
            nginx_conf.encode('utf-8'),
            server_ip, "root"
        )
        
        # Start nginx container (without project-specific network)
        DockerExecuter.run_container(
            image="nginx:alpine",
            name="nginx",
            ports={"80": "80", "443": "443"},
            volumes=[
                "/local/nginx/nginx.conf:/etc/nginx/nginx.conf:ro",
                "/local/nginx/conf.d:/etc/nginx/conf.d:ro",
                "/local/nginx/stream.d:/etc/nginx/stream.d:ro"
            ],
            restart_policy="unless-stopped",
            server_ip=server_ip,
            user="root"
        )
        
        log(f"Nginx container installed on {server_ip}")

    @staticmethod
    def create_droplets(
        count: int,
        region: str,
        cpu: int,
        memory: int,
        tags: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Create multiple droplets in parallel.
        
        Returns:
            List of dicts: [{droplet_id, ip, private_ip, zone, cpu, memory, created}, ...]
        """
        log(f"Creating {count} droplets in {region} (parallel execution)")
        Logger.start()
        
        droplet_ids = []
        
        # Create droplets in parallel
        with ThreadPoolExecutor(max_workers=min(count, 10)) as executor:
            futures = []
            
            for i in range(count):
                name = f"droplet-{region}-{int(time.time())}-{i}"
                future = executor.submit(
                    DOManager.create_droplet,
                    name, region, cpu, memory, tags
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
            info = DOManager.get_droplet_info(droplet_id)
            droplets_info.append(info)
        
        Logger.end()
        log(f"Successfully created {len(droplets_info)}/{count} droplets")
        
        return droplets_info
    
    @staticmethod
    def destroy_droplet(droplet_id: str) -> bool:
        """Destroy a droplet"""
        try:
            log(f"Destroying droplet {droplet_id}")
            DOManager._api_request("DELETE", f"/droplets/{droplet_id}")
            log(f"Droplet {droplet_id} destroyed")
            return True
        except Exception as e:
            log(f"Failed to destroy droplet {droplet_id}: {e}")
            return False
    
    @staticmethod
    def get_droplet_info(droplet_id: str) -> Dict[str, Any]:
        """
        Get droplet details.
        
        Returns:
            {droplet_id, name, ip, private_ip, region, size, status, created_at, tags, cpu, memory}
        """
        response = DOManager._api_request("GET", f"/droplets/{droplet_id}")
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
    def list_droplets(tags: List[str] = None) -> List[Dict[str, Any]]:
        """List all droplets, optionally filtered by tags"""
        if os.getenv("DIGITALOCEAN_API_TOKEN") is None:
            return []
        
        # Default to only listing "Infra" tagged droplets
        if tags is None:
            tags = ["Infra"]
        
        endpoint = "/droplets"
        
        # DigitalOcean API requires tag filtering via query params
        if tags:
            tag_query = "&".join([f"tag_name={tag}" for tag in tags])
            endpoint = f"/droplets?{tag_query}"
        
        response = DOManager._api_request("GET", endpoint)
        droplets = response.get('droplets', [])
        
        # Double-check filtering (in case API filtering isn't working)
        if tags:
            filtered = []
            for d in droplets:
                droplet_tags = d.get('tags', [])
                # Check if all required tags are present
                if all(tag in droplet_tags for tag in tags):
                    filtered.append(d)
            droplets = filtered
        
        return [DOManager.get_droplet_info(str(d['id'])) for d in droplets]
    
    @staticmethod
    def wait_for_droplet_active(droplet_id: str, timeout: int = 180) -> bool:
        """Wait for droplet to reach 'active' status"""
        log(f"Waiting for droplet {droplet_id} to become active...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            info = DOManager.get_droplet_info(droplet_id)
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
                # Use existing CommandExecuter which handles cross-platform SSH
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
            # Wait for cloud-init to complete (Ubuntu's first-boot initialization)
            log(f"Waiting for cloud-init to complete on {ip}...")
            
            # More robust cloud-init wait with retries
            for attempt in range(3):
                try:
                    CommandExecuter.run_cmd(
                        "cloud-init status --wait || timeout 300 bash -c 'while [ ! -f /var/lib/cloud/instance/boot-finished ]; do sleep 2; done'",
                        ip, user
                    )
                    log(f"Cloud-init completed on {ip}")
                    break
                except Exception as e:
                    if attempt < 2:
                        log(f"Waiting for cloud-init (attempt {attempt + 1}/3)...")
                        time.sleep(30)  # Wait 30 seconds before retry
                    else:
                        log(f"Cloud-init wait failed after 3 attempts: {e}")
                        return False
            
            # Additional stabilization wait
            time.sleep(10)
            
            # Rest of the Docker installation code...
            log(f"Waiting for apt locks to clear on {ip}...")
            wait_commands = [
                # Wait up to 10 minutes for dpkg lock
                "timeout 600 bash -c 'while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do echo \"Waiting for dpkg lock...\"; sleep 5; done'",
                # Wait for apt lists lock
                "timeout 600 bash -c 'while fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do echo \"Waiting for apt lists lock...\"; sleep 5; done'",
                # Kill any stuck processes
                "pkill -9 unattended-upgrade || true",
                "pkill -9 apt-get || true",
                # Final wait
                "sleep 5"
            ]
            
            for cmd in wait_commands:
                try:
                    CommandExecuter.run_cmd(cmd, ip, user)
                except Exception as e:
                    log(f"Warning during lock wait: {e}")
            
            # Use Docker's official installation script
            commands = [
                # Download Docker install script
                "curl -fsSL https://get.docker.com -o get-docker.sh",
                
                # Run installation with retry logic
                "for i in 1 2 3; do sh get-docker.sh && break || sleep 10; done",
                
                # Cleanup
                "rm get-docker.sh",
                
                # Start and enable Docker service
                "systemctl start docker",
                "systemctl enable docker",
                
                # Wait for Docker daemon
                "timeout 30 bash -c 'until docker ps >/dev/null 2>&1; do echo \"Waiting for Docker daemon...\"; sleep 2; done'",
                
                # Verify
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

    @staticmethod
    def update_droplet_tags(droplet_id: int, add_tags: List[str] = None, remove_tags: List[str] = None):
        """
        Update tags for a droplet using DigitalOcean's tag resource API.
        
        DigitalOcean doesn't support updating tags via PUT/PATCH on droplets (returns 405).
        Instead, we must use POST/DELETE on /tags/{name}/resources endpoints.
        
        Args:
            droplet_id: The droplet ID  
            add_tags: Tags to add to the droplet
            remove_tags: Tags to remove from the droplet
        """
        try:
            droplet_id_str = str(droplet_id)
            
            # Step 1: Remove old tags first (if any)
            if remove_tags:
                for tag in remove_tags:
                    try:
                        resource_data = {
                            "resources": [
                                {
                                    "resource_id": droplet_id_str,
                                    "resource_type": "droplet"
                                }
                            ]
                        }
                        
                        # Make the DELETE request - it returns 204 No Content on success
                        url = f"{DOManager.API_BASE}/tags/{tag}/resources"
                        headers = DOManager._get_headers()
                        response = requests.delete(url, headers=headers, json=resource_data, timeout=30)
                        
                        if response.status_code in [204, 200]:
                            log(f"Removed tag '{tag}' from droplet {droplet_id}")
                        else:
                            # Try to get error details
                            try:
                                error_detail = response.json()
                                log(f"Warning: Could not remove tag '{tag}': {error_detail}")
                            except:
                                log(f"Warning: Could not remove tag '{tag}': HTTP {response.status_code}")
                                
                    except Exception as e:
                        log(f"Warning: Could not remove tag '{tag}' from droplet {droplet_id}: {e}")
            
            # Step 2: Ensure all tags we want to add exist in the account
            if add_tags:
                for tag in add_tags:
                    try:
                        DOManager._api_request("POST", "/tags", {"name": tag})
                        log(f"Created tag '{tag}'")
                    except Exception as e:
                        # Tag might already exist (422 status), which is fine
                        error_str = str(e).lower()
                        if "422" not in error_str and "already exists" not in error_str:
                            log(f"Warning: Could not create tag '{tag}': {e}")
            
            # Step 3: Add new tags to droplet via tag resources endpoint
            if add_tags:
                for tag in add_tags:
                    try:
                        resource_data = {
                            "resources": [
                                {
                                    "resource_id": droplet_id_str,
                                    "resource_type": "droplet"
                                }
                            ]
                        }
                        
                        # Make the POST request - it returns 204 No Content on success
                        url = f"{DOManager.API_BASE}/tags/{tag}/resources"
                        headers = DOManager._get_headers()
                        response = requests.post(url, headers=headers, json=resource_data, timeout=30)
                        
                        if response.status_code in [204, 200]:
                            log(f"Added tag '{tag}' to droplet {droplet_id}")
                        else:
                            # Try to get error details
                            try:
                                error_detail = response.json()
                                log(f"Warning: Could not add tag '{tag}': {error_detail}")
                            except:
                                log(f"Warning: Could not add tag '{tag}': HTTP {response.status_code}")
                                
                    except Exception as e:
                        log(f"Warning: Could not add tag '{tag}' to droplet {droplet_id}: {e}")
                
        except Exception as e:
            log(f"Error updating tags for droplet {droplet_id}: {e}")
