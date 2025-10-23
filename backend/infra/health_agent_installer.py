from pathlib import Path
import time

from execute_cmd import CommandExecuter
from logger import Logger
import secrets

def log(msg):
    Logger.log(msg)

class HealthAgentInstaller:
    """Install health agent as systemd service"""
    
    DEPENDENCIES = [
        "flask",
        "requests"
    ]
    
    @staticmethod
    def install_on_server(server_ip: str, user: str = "root") -> bool:
        """
        Install health agent on server.
        
        Process:
        1. Install Python dependencies (flask, requests)
        2. Copy health_agent.py to /usr/local/bin/
        3. Generate and store API key
        4. Create systemd service file
        5. Configure firewall (port 9999, VPC only)
        6. Enable and start service
        7. Verify service is running
        
        Args:
            server_ip: Target server IP
            user: SSH user (default: root)
            
        Returns:
            True if installation successful
        """
        log(f"Installing health agent on {server_ip}...")
        Logger.start()
        
        try:
            # 1. Install dependencies
            deps = " ".join(HealthAgentInstaller.DEPENDENCIES)
            log(f"Installing dependencies: {deps}")
            CommandExecuter.run_cmd(
                f"pip3 install --break-system-packages {deps}",
                server_ip, user, timeout=300
            )
            log("Dependencies installed")
            
            # 2. Copy health agent script
            agent_script_path = Path(__file__).parent / "health_agent.py"
            if not agent_script_path.exists():
                raise FileNotFoundError(f"health_agent.py not found at {agent_script_path}")
            
            script_content = agent_script_path.read_text()
            CommandExecuter.run_cmd_with_stdin(
                "cat > /usr/local/bin/health_agent.py && chmod +x /usr/local/bin/health_agent.py",
                script_content.encode('utf-8'),
                server_ip, user
            )
            log("Health agent script copied to /usr/local/bin/")
            
            # 3. Generate and store API key
            api_key = secrets.token_urlsafe(32)
            CommandExecuter.run_cmd_with_stdin(
                "mkdir -p /etc/health-agent && "
                "cat > /etc/health-agent/api-key && "
                "chmod 600 /etc/health-agent/api-key",
                api_key.encode('utf-8'),
                server_ip, user
            )
            log("API key generated and stored in /etc/health-agent/")
            
            # 4. Create systemd service file
            service_content = """[Unit]
Description=Health Agent API
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/usr/local/bin
ExecStart=/usr/bin/python3 /usr/local/bin/health_agent.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
            CommandExecuter.run_cmd_with_stdin(
                "cat > /etc/systemd/system/health-agent.service",
                service_content.encode('utf-8'),
                server_ip, user
            )
            log("Systemd service file created")
            
            # 5. Configure firewall (allow port 9999 from VPC only)
            log("Configuring firewall...")
            CommandExecuter.run_cmd(
                "ufw allow from 10.0.0.0/16 to any port 9999 comment 'Health Agent VPC access'",
                server_ip, user
            )
            log("Firewall configured (port 9999 accessible from VPC only)")
            
            # 6. Enable and start service
            log("Enabling and starting service...")
            CommandExecuter.run_cmd(
                "systemctl daemon-reload && "
                "systemctl enable health-agent && "
                "systemctl start health-agent",
                server_ip, user
            )
            log("Service enabled and started")
            
            # 7. Wait a moment for service to start            
            time.sleep(3)
            
            # 8. Verify service is running
            result = CommandExecuter.run_cmd(
                "systemctl is-active health-agent",
                server_ip, user
            )
            
            if "active" not in str(result).lower():
                raise Exception("Service not running after installation")
            
            log(f"✓ Health agent successfully installed on {server_ip}")
            log(f"  - Service: health-agent.service")
            log(f"  - Port: 9999 (VPC only)")
            log(f"  - API Key stored in: /etc/health-agent/api-key")
            
            Logger.end()
            return True
            
        except Exception as e:
            log(f"❌ Failed to install health agent on {server_ip}: {e}")
            Logger.end()
            return False
    
    @staticmethod
    def remove_from_server(server_ip: str, user: str = "root") -> bool:
        """
        Remove health agent from server.
        
        Args:
            server_ip: Target server IP
            user: SSH user (default: root)
            
        Returns:
            True if removal successful
        """
        log(f"Removing health agent from {server_ip}...")
        
        try:
            # Stop and disable service
            CommandExecuter.run_cmd(
                "systemctl stop health-agent || true && "
                "systemctl disable health-agent || true",
                server_ip, user
            )
            
            # Remove files
            CommandExecuter.run_cmd(
                "rm -f /etc/systemd/system/health-agent.service && "
                "rm -f /usr/local/bin/health_agent.py && "
                "rm -rf /etc/health-agent && "
                "systemctl daemon-reload",
                server_ip, user
            )
            
            # Remove firewall rule
            CommandExecuter.run_cmd(
                "ufw delete allow from 10.0.0.0/16 to any port 9999 || true",
                server_ip, user
            )
            
            log(f"✓ Health agent removed from {server_ip}")
            return True
            
        except Exception as e:
            log(f"Failed to remove health agent from {server_ip}: {e}")
            return False
    
    @staticmethod
    def get_api_key(server_ip: str, user: str = "root") -> str:
        """
        Retrieve API key from server.
        
        Args:
            server_ip: Target server IP
            user: SSH user (default: root)
            
        Returns:
            API key string
        """
        result = CommandExecuter.run_cmd(
            "cat /etc/health-agent/api-key",
            server_ip, user
        )
        return result.strip()
    
    @staticmethod
    def verify_installation(server_ip: str, user: str = "root") -> bool:
        """
        Verify health agent is installed and running.
        
        Args:
            server_ip: Target server IP
            user: SSH user (default: root)
            
        Returns:
            True if agent is running properly
        """
        try:
            # Check service status
            result = CommandExecuter.run_cmd(
                "systemctl is-active health-agent",
                server_ip, user
            )
            
            if "active" not in str(result).lower():
                return False
            
            # Check if API key file exists
            CommandExecuter.run_cmd(
                "test -f /etc/health-agent/api-key",
                server_ip, user
            )
            
            # Check if script exists
            CommandExecuter.run_cmd(
                "test -f /usr/local/bin/health_agent.py",
                server_ip, user
            )
            
            return True
            
        except Exception:
            return False