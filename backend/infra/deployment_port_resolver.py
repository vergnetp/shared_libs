import os
import hashlib
from typing import List
from logger import Logger


def log(msg):
    Logger.log(msg)


class DeploymentPortResolver:

    @staticmethod
    def extract_ports_from_dockerfile(dockerfile_path: str) -> List[str]:
        """Extract EXPOSE ports from a Dockerfile."""
        ports = set()
        if os.path.exists(dockerfile_path):
            try:
                with open(dockerfile_path, "r") as f:
                    for line in f:
                        stripped = line.strip()
                        if stripped.upper().startswith("EXPOSE"):
                            parts = stripped.split()[1:]
                            for part in parts:
                                port = part.split("/")[0]
                                if port.isdigit():
                                    ports.add(port)
            except Exception as e:
                log(f"Warning: Could not read Dockerfile {dockerfile_path}: {e}")
        return list(ports)

    @staticmethod
    def get_default_ports(service_name: str) -> List[str]:
        """Get conventional ports for common service types."""
        defaults = {
            "backend": ["8000"], "api": ["8000"], "server": ["8000"],
            "frontend": ["3000"], "web": ["3000"], "ui": ["3000"], "react": ["3000"],
            "database": ["5432"], "postgres": ["5432"], "db": ["5432"],
            "mysql": ["3306"], "mariadb": ["3306"],
            "redis": ["6379"], "cache": ["6379"],
            "mongo": ["27017"], "mongodb": ["27017"],
            "elasticsearch": ["9200"], "opensearch": ["9200"],
            "nginx": ["80"], "apache": ["80"], "httpd": ["80"],
        }
        return defaults.get(service_name.lower(), [])

    @staticmethod
    def get_container_ports(service_name: str, dockerfile_path: str = None) -> List[str]:
        """Auto-detect ports from Dockerfile or use conventions."""
        ports = DeploymentPortResolver.extract_ports_from_dockerfile(dockerfile_path) if dockerfile_path else []
        if ports:
            log(f"Auto-detected ports for {service_name} from {dockerfile_path}: {ports}")
            return ports
        ports = DeploymentPortResolver.get_default_ports(service_name)
        if ports:
            log(f"Using conventional ports for {service_name}: {ports}")
        else:
            log(f"No ports detected for {service_name}, service may not expose ports.")
        return ports

    @staticmethod
    def generate_host_port(
        project_name: str, env: str, service_name: str, container_port: str, base_port: int = 8000
    ) -> int:
        """Generate deterministic host port for mapping."""
        hash_input = f"{project_name}_{env}_{service_name}_{container_port}"
        hash_value = int(hashlib.md5(hash_input.encode()).hexdigest()[:8], 16)
        port_offset = hash_value % 1000
        return base_port + port_offset
