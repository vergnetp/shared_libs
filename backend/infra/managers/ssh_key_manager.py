"""
SSH Key Management

Handles SSH key generation, management, and upload to DigitalOcean.
Cross-platform compatible SSH key operations.
"""

import os
import subprocess
import digitalocean
from pathlib import Path
from typing import Optional, List


class SSHKeyManager:
    """
    Manages SSH keys for infrastructure access
    """
    
    def __init__(self, key_name: str = "infrastructure_key", do_manager: digitalocean.Manager = None):
        self.key_name = key_name
        self.do_manager = do_manager
        self.key_path = self._get_ssh_key_path()
        self.public_key_path = Path(f"{self.key_path}.pub")
        
    def _get_ssh_key_path(self) -> Path:
        """Get SSH key path (cross-platform)"""
        home = Path.home()
        ssh_dir = home / ".ssh"
        
        # Create .ssh directory if it doesn't exist
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        
        return ssh_dir / self.key_name
    
    def key_exists(self) -> bool:
        """Check if SSH key pair exists"""
        return self.key_path.exists() and self.public_key_path.exists()
    
    def generate_key_pair(self, force: bool = False) -> bool:
        """Generate SSH key pair if it doesn't exist"""
        
        if self.key_exists() and not force:
            print(f"SSH key pair already exists at {self.key_path}")
            return True
        
        try:
            # Generate SSH key pair
            cmd = [
                'ssh-keygen',
                '-t', 'rsa',
                '-b', '4096',
                '-f', str(self.key_path),
                '-N', '',  # No passphrase
                '-C', f'infrastructure-{self.key_name}'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                # Set proper permissions
                self._set_key_permissions()
                print(f"SSH key pair generated: {self.key_path}")
                return True
            else:
                print(f"Failed to generate SSH key: {result.stderr}")
                return False
                
        except FileNotFoundError:
            print("ssh-keygen not found. Please install OpenSSH client.")
            return False
        except Exception as e:
            print(f"Error generating SSH key: {e}")
            return False
    
    def _set_key_permissions(self):
        """Set proper permissions on SSH key files"""
        if os.name != 'nt':  # Not Windows
            # Private key: 600 (read/write for owner only)
            os.chmod(self.key_path, 0o600)
            # Public key: 644 (read for all, write for owner)
            os.chmod(self.public_key_path, 0o644)
    
    def get_public_key(self) -> Optional[str]:
        """Get public key content"""
        if not self.public_key_path.exists():
            return None
        
        try:
            return self.public_key_path.read_text().strip()
        except Exception as e:
            print(f"Error reading public key: {e}")
            return None
    
    def get_private_key_path(self) -> str:
        """Get private key path for SSH connections"""
        return str(self.key_path)
    
    def upload_to_digitalocean(self, force: bool = False) -> Optional[str]:
        """Upload SSH key to DigitalOcean"""
        
        if not self.do_manager:
            print("DigitalOcean manager not provided")
            return None
        
        public_key = self.get_public_key()
        if not public_key:
            print("Public key not found")
            return None
        
        # Check if key already exists in DigitalOcean
        existing_keys = self.do_manager.get_all_sshkeys()
        for key in existing_keys:
            if key.name == self.key_name:
                if force:
                    # Delete existing key
                    key.destroy()
                    print(f"Deleted existing SSH key {self.key_name} from DigitalOcean")
                else:
                    print(f"SSH key {self.key_name} already exists in DigitalOcean")
                    return str(key.id)
        
        # Upload new key
        try:
            ssh_key = digitalocean.SSHKey(
                token=self.do_manager.token,
                name=self.key_name,
                public_key=public_key
            )
            
            ssh_key.create()
            print(f"SSH key {self.key_name} uploaded to DigitalOcean")
            return str(ssh_key.id)
            
        except Exception as e:
            print(f"Error uploading SSH key to DigitalOcean: {e}")
            return None
    
    def get_digitalocean_key_id(self) -> Optional[str]:
        """Get DigitalOcean SSH key ID"""
        
        if not self.do_manager:
            return None
        
        try:
            keys = self.do_manager.get_all_sshkeys()
            for key in keys:
                if key.name == self.key_name:
                    return str(key.id)
        except Exception as e:
            print(f"Error getting DigitalOcean SSH key ID: {e}")
        
        return None
    
    def ensure_key_ready(self) -> Optional[str]:
        """Ensure SSH key is generated and uploaded to DigitalOcean"""
        
        # Generate key pair if it doesn't exist
        if not self.key_exists():
            if not self.generate_key_pair():
                return None
        
        # Upload to DigitalOcean if manager is provided
        if self.do_manager:
            return self.upload_to_digitalocean()
        
        return self.get_private_key_path()
    
    def test_connection(self, host: str, user: str = "root") -> bool:
        """Test SSH connection to a host"""
        
        if not self.key_exists():
            print("SSH key pair not found")
            return False
        
        try:
            cmd = [
                'ssh',
                '-i', str(self.key_path),
                '-o', 'ConnectTimeout=10',
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'BatchMode=yes',  # Non-interactive
                f'{user}@{host}',
                'echo "SSH connection successful"'
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            
            if result.returncode == 0:
                print(f"SSH connection to {host} successful")
                return True
            else:
                print(f"SSH connection to {host} failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"SSH connection to {host} timed out")
            return False
        except Exception as e:
            print(f"Error testing SSH connection: {e}")
            return False
    
    def add_additional_key_to_server(self, host: str, additional_public_key: str, 
                                   key_name: str, user: str = "root") -> bool:
        """Add an additional SSH key to a server's authorized_keys"""
        
        if not self.key_exists():
            print("Primary SSH key pair not found")
            return False
        
        try:
            # Command to add key to authorized_keys
            ssh_cmd = [
                'ssh',
                '-i', str(self.key_path),
                '-o', 'StrictHostKeyChecking=no',
                f'{user}@{host}',
                f'echo "{additional_public_key}" >> ~/.ssh/authorized_keys && ' +
                f'echo "# {key_name}" >> ~/.ssh/authorized_keys'
            ]
            
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                print(f"Additional SSH key '{key_name}' added to {host}")
                return True
            else:
                print(f"Failed to add SSH key to {host}: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"Timeout adding SSH key to {host}")
            return False
        except Exception as e:
            print(f"Error adding SSH key to {host}: {e}")
            return False
    
    def execute_remote_command(self, host: str, command: str, user: str = "root", timeout: int = 30) -> tuple[bool, str, str]:
        """Execute a command on a remote host via SSH"""
        
        if not self.key_exists():
            return False, "", "SSH key pair not found"
        
        try:
            ssh_cmd = [
                'ssh',
                '-i', str(self.key_path),
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'ConnectTimeout=10',
                f'{user}@{host}',
                command
            ]
            
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=timeout)
            
            return (
                result.returncode == 0,
                result.stdout,
                result.stderr
            )
            
        except subprocess.TimeoutExpired:
            return False, "", f"Command timed out after {timeout} seconds"
        except Exception as e:
            return False, "", f"Error executing command: {e}"
    
    def copy_file_to_server(self, host: str, local_file: str, remote_file: str, user: str = "root") -> bool:
        """Copy a file to a remote server via SCP"""
        
        if not self.key_exists():
            print("SSH key pair not found")
            return False
        
        try:
            scp_cmd = [
                'scp',
                '-i', str(self.key_path),
                '-o', 'StrictHostKeyChecking=no',
                local_file,
                f'{user}@{host}:{remote_file}'
            ]
            
            result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0:
                print(f"File {local_file} copied to {host}:{remote_file}")
                return True
            else:
                print(f"Failed to copy file to {host}: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            print(f"File copy to {host} timed out")
            return False
        except Exception as e:
            print(f"Error copying file to {host}: {e}")
            return False
