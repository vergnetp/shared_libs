"""
Cloud-Init Script Builder

Single source of truth for generating cloud-init scripts for droplet provisioning.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Callable


# =============================================================================
# EMERGENCY ADMIN ACCESS
# =============================================================================
# TODO: Remove before production / real clients
# These IPs can SSH into all droplets for debugging
EMERGENCY_ADMIN_IPS = [
    "90.126.99.163",  # Phil - France (update when back in London, then recreate snapshot)
]
# =============================================================================


@dataclass
class CloudInitConfig:
    """Configuration for cloud-init script generation."""
    install_docker: bool = True
    apt_packages: List[str] = field(default_factory=list)
    pip_packages: List[str] = field(default_factory=list)
    docker_images: List[str] = field(default_factory=list)
    custom_commands: List[str] = field(default_factory=list)
    install_node_agent: bool = True
    node_agent_api_key: Optional[str] = None
    # Security options for node_agent
    node_agent_allowed_ips: Optional[List[str]] = None  # IP allowlist
    node_agent_require_auth_always: bool = False  # Disable VPC auth bypass


def build_cloudinit_script(
    config: CloudInitConfig,
    log: Callable[[str], None] = None,
) -> tuple[str, Optional[str]]:
    """
    Build a cloud-init script from configuration.
    
    Args:
        config: CloudInitConfig with all options
        log: Optional callback for logging progress
        
    Returns:
        Tuple of (script_content, api_key) where api_key is set if node_agent installed
    """
    if log is None:
        log = lambda x: None
    
    lines = [
        "#!/bin/bash",
        "set -e",
        "",
        "# Verbose logging function",
        "log() { echo \"[$(date '+%H:%M:%S')] $1\"; }",
        "",
        "export DEBIAN_FRONTEND=noninteractive",
        "",
        "log '========================================='",
        "log 'CLOUD-INIT USER SCRIPT STARTING'",
        "log '========================================='",
        "",
        "# Wait for apt locks to be released (other cloud-init modules may be using apt)",
        "log 'Waiting for apt locks...'",
        "while fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do",
        "    log '  Waiting for dpkg lock...'",
        "    sleep 5",
        "done",
        "while fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do",
        "    log '  Waiting for apt lists lock...'",
        "    sleep 5",
        "done",
        "log 'Apt locks released'",
        "",
        "# Update system",
        "log 'Running apt-get update...'",
        "apt-get update -y",
        "log 'Running apt-get upgrade...'",
        "apt-get upgrade -y",
        "log 'System update complete'",
        "",
    ]
    
    # Firewall setup - restrict SSH to emergency admin IPs only
    if EMERGENCY_ADMIN_IPS:
        lines.extend([
            "# Firewall setup - restrict SSH access",
            "log '========================================='",
            "log 'CONFIGURING FIREWALL'",
            "log '========================================='",
            "apt-get install -y ufw",
            "ufw default deny incoming",
            "ufw default allow outgoing",
            "# Allow node agent port from anywhere",
            "ufw allow 9999/tcp",
        ])
        for ip in EMERGENCY_ADMIN_IPS:
            lines.append(f"# Emergency admin SSH access")
            lines.append(f"ufw allow from {ip} to any port 22")
            lines.append(f"log 'Allowed SSH from emergency admin: {ip}'")
        lines.extend([
            "ufw --force enable",
            "log 'Firewall configured - SSH restricted to admin IPs only'",
            "",
        ])
        log(f"  🔒 Firewall: SSH restricted to {len(EMERGENCY_ADMIN_IPS)} admin IP(s)")
    
    # Docker installation
    if config.install_docker:
        lines.extend([
            "# Install Docker",
            "log '========================================='",
            "log 'INSTALLING DOCKER'",
            "log '========================================='",
            "log 'Downloading Docker install script...'",
            "curl -fsSL https://get.docker.com -o /tmp/get-docker.sh",
            "log 'Running Docker install script...'",
            "sh /tmp/get-docker.sh",
            "log 'Enabling Docker service...'",
            "systemctl enable docker",
            "log 'Starting Docker service...'",
            "systemctl start docker",
            "log 'Verifying Docker installation...'",
            "docker --version",
            "log 'Docker installation COMPLETE'",
            "",
        ])
        log("  📦 Docker will be installed")
    
    # APT packages
    if config.apt_packages:
        pkg_list = " ".join(config.apt_packages)
        lines.extend([
            "# Install APT packages",
            "log '========================================='",
            f"log 'INSTALLING APT PACKAGES: {pkg_list}'",
            "log '========================================='",
            "apt-get update",
            f"log 'Installing: {pkg_list}'",
            f"apt-get install -y {pkg_list}",
            "log 'APT packages COMPLETE'",
            "",
        ])
        log(f"  📦 APT packages: {', '.join(config.apt_packages)}")
    
    # Pip packages
    if config.pip_packages:
        pkg_list = " ".join(config.pip_packages)
        lines.extend([
            "# Install pip packages",
            "log '========================================='",
            f"log 'INSTALLING PIP PACKAGES: {pkg_list}'",
            "log '========================================='",
            "log 'Ensuring python3-pip is installed...'",
            "apt-get install -y python3-pip || true",
            "log 'pip3 version:'",
            "pip3 --version || echo 'pip3 not found!'",
            f"log 'Installing pip packages: {pkg_list}'",
            f"pip3 install --ignore-installed --break-system-packages {pkg_list}",
            "log 'Pip packages COMPLETE'",
            "",
        ])
        log(f"  📦 Pip packages: {', '.join(config.pip_packages)}")
    
    # Docker images
    if config.docker_images:
        lines.extend([
            "# Pull Docker images",
            "log '========================================='",
            f"log 'PULLING {len(config.docker_images)} DOCKER IMAGES'",
            "log '========================================='",
        ])
        for img in config.docker_images:
            lines.extend([
                f"log 'Pulling {img}...'",
                f"docker pull {img}",
                f"log '{img} pulled successfully'",
            ])
        lines.extend([
            "log 'Docker images COMPLETE'",
            "",
        ])
        log(f"  🐳 Docker images: {', '.join(config.docker_images)}")
    
    # Custom commands
    if config.custom_commands:
        lines.extend([
            "# Custom commands",
            "log '========================================='",
            f"log 'RUNNING {len(config.custom_commands)} CUSTOM COMMANDS'",
            "log '========================================='",
        ])
        for i, cmd in enumerate(config.custom_commands):
            lines.extend([
                f"log 'Custom command {i+1}: {cmd[:50]}...'",
                cmd,
            ])
        lines.append("")
        log(f"  ⚙️ Custom commands: {len(config.custom_commands)}")
    
    # Node agent
    api_key = None
    if config.install_node_agent:
        import secrets
        from ..node_agent import get_node_agent_install_script
        
        api_key = config.node_agent_api_key or secrets.token_urlsafe(32)
        lines.extend([
            "log '========================================='",
            "log 'INSTALLING NODE AGENT'",
            "log '========================================='",
        ])
        lines.append(get_node_agent_install_script(
            api_key,
            allowed_ips=config.node_agent_allowed_ips,
            require_auth_always=config.node_agent_require_auth_always,
        ))
        lines.extend([
            "log 'Verifying node-agent service...'",
            "systemctl status node-agent --no-pager || echo 'node-agent status check done'",
            "log 'Node agent installation COMPLETE'",
        ])
        log(f"  🔐 Node agent will be installed (API key: {api_key[:8]}...)")
        if config.node_agent_allowed_ips:
            log(f"  🔒 IP allowlist: {config.node_agent_allowed_ips}")
        if config.node_agent_require_auth_always:
            log(f"  🔒 VPC auth bypass: DISABLED")
    
    # Cleanup and completion marker
    lines.extend([
        "",
        "log '========================================='",
        "log 'CLEANUP AND FINALIZATION'",
        "log '========================================='",
        "log 'Cleaning apt cache...'",
        "apt-get clean",
        "rm -rf /var/lib/apt/lists/*",
        "",
        "log 'Creating completion marker...'",
        "touch /tmp/snapshot-setup-complete",
        "",
        "log '========================================='",
        "log 'CLOUD-INIT SCRIPT COMPLETED SUCCESSFULLY'",
        "log '========================================='",
    ])
    
    return "\n".join(lines), api_key


# Presets for common configurations
SNAPSHOT_PRESETS = {
    "base": {
        "description": "Base image - Docker + common services (auto-created)",
        "auto_create": True,  # Created automatically when user adds DO token
        "config": CloudInitConfig(
            install_docker=True,
            docker_images=[
                "nginx:alpine",
                "postgres:16-alpine",
                "redis:7-alpine",
                "opensearchproject/opensearch:2",
                "qdrant/qdrant:latest",
                "python:3.11-slim",
                "node:20-alpine",
            ],
            apt_packages=["htop", "curl", "git", "jq", "vim"],
            # Note: local/base:latest is NOT created here - it's only for custom snapshots
            # where user builds their own base image with pre-installed dependencies
        ),
    },
    "minimal": {
        "description": "Docker only, no pre-pulled images",
        "config": CloudInitConfig(
            install_docker=True,
            docker_images=[],
            apt_packages=[],
        ),
    },
    "standard": {
        "description": "Docker + postgres, redis, nginx",
        "config": CloudInitConfig(
            install_docker=True,
            docker_images=["postgres:16-alpine", "redis:7-alpine", "nginx:alpine"],
            apt_packages=["htop", "vim", "curl", "git", "python3-pip"],
        ),
    },
    "python": {
        "description": "Docker + Python dev tools",
        "config": CloudInitConfig(
            install_docker=True,
            docker_images=["postgres:16-alpine", "redis:7-alpine", "python:3.11-slim"],
            apt_packages=["htop", "vim", "curl", "git", "python3-pip", "python3-venv"],
            pip_packages=["httpx", "pydantic", "uvicorn", "fastapi"],
        ),
    },
    "full": {
        "description": "Docker + all common services",
        "config": CloudInitConfig(
            install_docker=True,
            docker_images=[
                "postgres:16-alpine",
                "redis:7-alpine",
                "nginx:alpine",
                "python:3.11-slim",
                "node:20-alpine",
            ],
            apt_packages=["htop", "vim", "curl", "git", "python3-pip", "python3-venv", "jq"],
            pip_packages=["httpx", "pydantic"],
        ),
    },
}


def get_preset(name: str) -> CloudInitConfig:
    """Get a preset configuration by name."""
    if name not in SNAPSHOT_PRESETS:
        raise ValueError(f"Unknown preset: {name}. Available: {list(SNAPSHOT_PRESETS.keys())}")
    return SNAPSHOT_PRESETS[name]["config"]


def get_preset_info(name: str) -> dict:
    """Get preset info including description."""
    if name not in SNAPSHOT_PRESETS:
        raise ValueError(f"Unknown preset: {name}. Available: {list(SNAPSHOT_PRESETS.keys())}")
    preset = SNAPSHOT_PRESETS[name]
    config = preset["config"]
    return {
        "name": name,
        "description": preset["description"],
        "config": {
            "install_docker": config.install_docker,
            "docker_images": config.docker_images,
            "apt_packages": config.apt_packages,
            "pip_packages": config.pip_packages,
            "install_node_agent": config.install_node_agent,
        }
    }
