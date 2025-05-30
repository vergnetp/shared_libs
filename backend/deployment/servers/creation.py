import digitalocean
import paramiko
import time
import os
from typing import Optional, Dict, Any

class DigitalOceanManager:
    def __init__(self, api_token: str):
        """
        Initialize DigitalOcean manager
        
        Args:
            api_token: DigitalOcean API token from https://cloud.digitalocean.com/account/api/tokens
        """
        self.manager = digitalocean.Manager(token=api_token)
    
    def create_droplet(
        self,
        name: str,
        region: str = "lon1",
        size: str = "s-2vcpu-4gb",
        image: str = "ubuntu-24-04-x64",
        ssh_keys: list = None,
        wait_for_ready: bool = True
    ) -> Dict[str, Any]:
        """
        Create a new DigitalOcean droplet
        
        Args:
            name: Droplet name
            region: Region slug (nyc1, lon1, fra1, etc.)
            size: Size slug (s-1vcpu-1gb, s-2vcpu-4gb, etc.)
            image: Image slug (ubuntu-24-04-x64, etc.)
            ssh_keys: List of SSH key IDs/fingerprints to add
            wait_for_ready: Whether to wait for droplet to be ready
            
        Returns:
            Dict with droplet info including IP address
        """
        print(f"🚀 Creating droplet '{name}' in {region}...")
        
        # Get available SSH keys if none specified
        if ssh_keys is None:
            available_keys = self.manager.get_all_sshkeys()
            ssh_keys = [key.id for key in available_keys]
            print(f"📋 Using {len(ssh_keys)} available SSH keys")
        
        # Create droplet
        droplet = digitalocean.Droplet(
            token=self.manager.token,
            name=name,
            region=region,
            image=image,
            size_slug=size,
            ssh_keys=ssh_keys,
            backups=False,
            ipv6=False,
            user_data=None,
            private_networking=False
        )
        
        # Create the droplet
        droplet.create()
        print(f"⏳ Droplet created with ID: {droplet.id}")
        
        if wait_for_ready:
            print("⏳ Waiting for droplet to be ready...")
            actions = droplet.get_actions()
            for action in actions:
                action.wait()
            
            # Get updated droplet info
            droplet.load()
            
            # Wait a bit more for SSH to be ready
            print("⏳ Waiting for SSH to be ready...")
            time.sleep(30)
        
        return {
            "id": droplet.id,
            "name": droplet.name,
            "ip_address": droplet.ip_address,
            "status": droplet.status,
            "region": droplet.region["name"],
            "size": droplet.size_slug
        }
    
    def get_droplet_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get droplet info by name"""
        droplets = self.manager.get_all_droplets()
        for droplet in droplets:
            if droplet.name == name:
                return {
                    "id": droplet.id,
                    "name": droplet.name,
                    "ip_address": droplet.ip_address,
                    "status": droplet.status,
                    "region": droplet.region["name"],
                    "size": droplet.size_slug
                }
        return None
    
    def destroy_droplet(self, droplet_name: str) -> bool:
        """Destroy a droplet by name"""
        droplets = self.manager.get_all_droplets()
        for droplet in droplets:
            if droplet.name == droplet_name:
                droplet.destroy()
                print(f"💥 Destroyed droplet: {droplet_name}")
                return True
        print(f"❌ Droplet not found: {droplet_name}")
        return False


class ServerSetup:
    def __init__(self, host: str, username: str = "root", ssh_key_path: str = None):
        """
        Initialize server setup manager
        
        Args:
            host: Server IP address
            username: SSH username
            ssh_key_path: Path to private SSH key
        """
        self.host = host
        self.username = username
        self.ssh_key_path = ssh_key_path or os.path.expanduser("~/.ssh/id_rsa")
        self.ssh_client = None
    
    def connect(self, timeout: int = 30) -> bool:
        """Connect to server via SSH"""
        print(f"🔗 Connecting to {self.host}...")
        
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            # Try with SSH key first
            if os.path.exists(self.ssh_key_path):
                self.ssh_client.connect(
                    hostname=self.host,
                    username=self.username,
                    key_filename=self.ssh_key_path,
                    timeout=timeout
                )
            else:
                # Try without key (if droplet was created with DO SSH keys)
                self.ssh_client.connect(
                    hostname=self.host,
                    username=self.username,
                    timeout=timeout
                )
            
            print(f"✅ Connected to {self.host}")
            return True
            
        except Exception as e:
            print(f"❌ Failed to connect to {self.host}: {e}")
            return False
    
    def execute_command(self, command: str) -> tuple:
        """Execute command on remote server"""
        if not self.ssh_client:
            raise Exception("Not connected to server")
        
        stdin, stdout, stderr = self.ssh_client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        
        stdout_text = stdout.read().decode('utf-8').strip()
        stderr_text = stderr.read().decode('utf-8').strip()
        
        return exit_code, stdout_text, stderr_text
    
    def upload_ssh_keys(self, ssh_keys: Dict[str, str]) -> bool:
        """
        Upload SSH public keys to server
        
        Args:
            ssh_keys: Dict of {key_name: public_key_content}
        """
        print(f"🔑 Uploading {len(ssh_keys)} SSH keys...")
        
        try:
            # Ensure .ssh directory exists
            self.execute_command("mkdir -p ~/.ssh && chmod 700 ~/.ssh")
            
            # Read existing authorized_keys
            exit_code, existing_keys, _ = self.execute_command("cat ~/.ssh/authorized_keys 2>/dev/null || echo ''")
            
            # Add new keys
            for key_name, public_key in ssh_keys.items():
                if public_key not in existing_keys:
                    self.execute_command(f'echo "{public_key}" >> ~/.ssh/authorized_keys')
                    print(f"  ✅ Added key: {key_name}")
                else:
                    print(f"  ⏭️  Key already exists: {key_name}")
            
            # Set proper permissions
            self.execute_command("chmod 600 ~/.ssh/authorized_keys")
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to upload SSH keys: {e}")
            return False
    
    def install_docker(self) -> bool:
        """Install Docker if not already installed"""
        print("🐳 Checking Docker installation...")
        
        try:
            # Check if Docker is already installed
            exit_code, _, _ = self.execute_command("docker --version")
            if exit_code == 0:
                print("  ✅ Docker already installed")
                return True
            
            print("  📦 Installing Docker...")
            
            # Install Docker
            commands = [
                "apt-get update",
                "apt-get install -y ca-certificates curl gnupg lsb-release",
                "mkdir -p /etc/apt/keyrings",
                "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg",
                'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null',
                "apt-get update",
                "apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin",
                "systemctl enable docker",
                "systemctl start docker"
            ]
            
            for cmd in commands:
                print(f"    Running: {cmd}")
                exit_code, stdout, stderr = self.execute_command(cmd)
                if exit_code != 0:
                    print(f"    ❌ Failed: {stderr}")
                    return False
            
            # Verify installation
            exit_code, version, _ = self.execute_command("docker --version")
            if exit_code == 0:
                print(f"  ✅ Docker installed: {version}")
                return True
            else:
                print("  ❌ Docker installation verification failed")
                return False
                
        except Exception as e:
            print(f"❌ Failed to install Docker: {e}")
            return False
    
    def disconnect(self):
        """Close SSH connection"""
        if self.ssh_client:
            self.ssh_client.close()
            print(f"🔌 Disconnected from {self.host}")


def create_and_setup_droplet(
    do_token: str,
    droplet_name: str,
    ssh_keys: Dict[str, str],
    region: str = "lon1"
) -> str:
    """
    One-shot function to create droplet and set it up
    
    Returns:
        IP address of created droplet
    """
    # Create droplet
    do_manager = DigitalOceanManager(do_token)
    droplet_info = do_manager.create_droplet(
        name=droplet_name,
        region=region,
        wait_for_ready=True
    )
    
    # Setup server
    server = ServerSetup(droplet_info['ip_address'])
    if server.connect():
        server.upload_ssh_keys(ssh_keys)
        server.install_docker()
        server.disconnect()
    
    return droplet_info['ip_address']


def setup_existing_server(
    server_ip: str,
    ssh_keys: Dict[str, str],
    ssh_key_path: str = None
) -> bool:
    """
    Setup existing server with SSH keys and Docker
    
    Returns:
        True if successful
    """
    server = ServerSetup(server_ip, ssh_key_path=ssh_key_path)
    
    if not server.connect():
        return False
    
    success = True
    success &= server.upload_ssh_keys(ssh_keys)
    success &= server.install_docker()
    
    server.disconnect()
    return success

# Usage Examples
def main():
    # Your DigitalOcean API token
    DO_TOKEN = "your-digitalocean-api-token"
    
    # Initialize manager
    do_manager = DigitalOceanManager(DO_TOKEN)
    
    # 1. Create a new droplet
    droplet_info = do_manager.create_droplet(
        name="my-docker-server",
        region="lon1",
        size="s-2vcpu-4gb",
        wait_for_ready=True
    )
    
    print(f"✅ Droplet created: {droplet_info['ip_address']}")
    
    # 2. Set up the server
    server_ip = droplet_info['ip_address']
    
    # Your SSH keys to upload
    ssh_keys = {
        "my_laptop": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC... your-public-key",
        "deploy_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG... another-public-key"
    }
    
    # Setup server
    server = ServerSetup(server_ip, username="root")
    
    if server.connect():
        # Upload SSH keys
        server.upload_ssh_keys(ssh_keys)
        
        # Install Docker
        server.install_docker()
        
        # Test Docker
        exit_code, output, _ = server.execute_command("docker run hello-world")
        if exit_code == 0:
            print("✅ Docker test successful!")
        
        server.disconnect()
    
    return server_ip


if __name__ == "__main__":
    server_ip = main()
    print(f"🎉 Server ready at: {server_ip}")