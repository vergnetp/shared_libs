"""
Dockerfile Generator - Auto-generate Dockerfiles from source folders.

Detects service type and generates appropriate Dockerfile.
"""

from __future__ import annotations
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field


@dataclass
class DockerfileConfig:
    """Configuration for Dockerfile generation."""
    base_image: str = "python:3.11-slim"
    workdir: str = "/app"
    port: int = 8000
    cmd: Optional[List[str]] = None
    env_vars: Dict[str, str] = field(default_factory=dict)
    
    # For services with shared_libs
    shared_libs_path: Optional[str] = None  # Relative path to shared_libs
    
    # Extra files to copy
    extra_copy: List[str] = field(default_factory=list)


class DockerfileGenerator:
    """
    Generate Dockerfiles from source folders.
    
    Usage:
        gen = DockerfileGenerator()
        
        # Auto-detect and generate
        dockerfile = gen.generate("/path/to/service")
        
        # With config
        dockerfile = gen.generate("/path/to/service", DockerfileConfig(port=8001))
    """
    
    @staticmethod
    def detect_service_type(source_path: str) -> str:
        """
        Detect the type of service from source folder.
        
        Returns:
            str: Service type ('python-fastapi', 'python-flask', 'node', 'static', 'unknown')
        """
        path = Path(source_path)
        
        # Check for Python FastAPI (app_kernel style)
        if (path / "main.py").exists():
            main_content = (path / "main.py").read_text()
            if "fastapi" in main_content.lower() or "app_kernel" in main_content:
                return "python-fastapi"
            if "flask" in main_content.lower():
                return "python-flask"
        
        # Check for requirements.txt (generic Python)
        if (path / "requirements.txt").exists():
            return "python-generic"
        
        # Check for package.json (Node.js)
        if (path / "package.json").exists():
            return "node"
        
        # Check for static files only
        if (path / "index.html").exists():
            return "static"
        
        return "unknown"
    
    @staticmethod
    def detect_port(source_path: str) -> int:
        """Detect port from source code."""
        path = Path(source_path)
        
        # Check main.py for port
        main_py = path / "main.py"
        if main_py.exists():
            content = main_py.read_text()
            # Look for port= or PORT
            import re
            match = re.search(r'port[=:\s]+(\d+)', content, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        # Check config.py
        config_py = path / "config.py"
        if config_py.exists():
            content = config_py.read_text()
            import re
            match = re.search(r'port[=:\s]+(\d+)', content, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        return 8000  # Default
    
    @staticmethod
    def detect_shared_libs(source_path: str) -> Optional[str]:
        """
        Detect if service uses shared_libs and find relative path.
        
        Returns:
            Relative path to shared_libs from source, or None
        """
        path = Path(source_path).resolve()
        
        # Check main.py for shared_libs imports
        main_py = path / "main.py"
        if main_py.exists():
            content = main_py.read_text()
            if "from backend." in content or "import backend." in content:
                # Look for shared_libs in parent directories
                for parent in path.parents:
                    if (parent / "backend").exists():
                        # Calculate relative path
                        rel = os.path.relpath(parent, path)
                        return rel
        
        return None
    
    @staticmethod
    def generate(
        source_path: str,
        config: Optional[DockerfileConfig] = None,
    ) -> str:
        """
        Generate Dockerfile for a service.
        
        Args:
            source_path: Path to service source folder
            config: Optional configuration
            
        Returns:
            Dockerfile content as string
        """
        config = config or DockerfileConfig()
        path = Path(source_path)
        service_type = DockerfileGenerator.detect_service_type(source_path)
        
        if service_type == "python-fastapi":
            return DockerfileGenerator._generate_python_fastapi(path, config)
        elif service_type == "python-generic":
            return DockerfileGenerator._generate_python_generic(path, config)
        elif service_type == "node":
            return DockerfileGenerator._generate_node(path, config)
        elif service_type == "static":
            return DockerfileGenerator._generate_static(path, config)
        else:
            # Fallback to generic Python
            return DockerfileGenerator._generate_python_generic(path, config)
    
    @staticmethod
    def _generate_python_fastapi(path: Path, config: DockerfileConfig) -> str:
        """Generate Dockerfile for Python FastAPI service."""
        port = config.port or DockerfileGenerator.detect_port(str(path))
        service_name = path.name
        
        # Check if uses shared_libs
        shared_libs = config.shared_libs_path or DockerfileGenerator.detect_shared_libs(str(path))
        
        lines = [
            f"FROM {config.base_image}",
            "",
            f"WORKDIR {config.workdir}",
            "",
            "# Install dependencies",
        ]
        
        # Check for requirements.txt
        if (path / "requirements.txt").exists():
            lines.extend([
                "COPY requirements.txt .",
                "RUN pip install --no-cache-dir -r requirements.txt",
                "",
            ])
        else:
            # Default FastAPI deps
            lines.extend([
                "RUN pip install --no-cache-dir fastapi uvicorn[standard]",
                "",
            ])
        
        # Copy shared_libs if needed
        if shared_libs:
            lines.extend([
                "# Copy shared libraries",
                f"COPY {shared_libs}/backend ./backend",
                "",
            ])
        
        # Copy service code
        lines.extend([
            "# Copy service code",
            f"COPY {service_name} ./{service_name}",
            "",
        ])
        
        # Environment variables
        if config.env_vars:
            lines.append("# Environment variables")
            for key, value in config.env_vars.items():
                lines.append(f"ENV {key}={value}")
            lines.append("")
        
        # Expose port
        lines.extend([
            f"EXPOSE {port}",
            "",
        ])
        
        # Command
        if config.cmd:
            cmd_str = ", ".join(f'"{c}"' for c in config.cmd)
            lines.append(f"CMD [{cmd_str}]")
        else:
            lines.append(f'CMD ["uvicorn", "{service_name}.main:app", "--host", "0.0.0.0", "--port", "{port}"]')
        
        return "\n".join(lines)
    
    @staticmethod
    def _generate_python_generic(path: Path, config: DockerfileConfig) -> str:
        """Generate Dockerfile for generic Python service."""
        port = config.port or 8000
        
        lines = [
            f"FROM {config.base_image}",
            "",
            f"WORKDIR {config.workdir}",
            "",
        ]
        
        if (path / "requirements.txt").exists():
            lines.extend([
                "COPY requirements.txt .",
                "RUN pip install --no-cache-dir -r requirements.txt",
                "",
            ])
        
        lines.extend([
            "COPY . .",
            "",
            f"EXPOSE {port}",
            "",
        ])
        
        if config.cmd:
            cmd_str = ", ".join(f'"{c}"' for c in config.cmd)
            lines.append(f"CMD [{cmd_str}]")
        else:
            lines.append('CMD ["python", "main.py"]')
        
        return "\n".join(lines)
    
    @staticmethod
    def _generate_node(path: Path, config: DockerfileConfig) -> str:
        """Generate Dockerfile for Node.js service."""
        port = config.port or 3000
        
        lines = [
            "FROM node:20-slim",
            "",
            f"WORKDIR {config.workdir}",
            "",
            "COPY package*.json ./",
            "RUN npm ci --only=production",
            "",
            "COPY . .",
            "",
            f"EXPOSE {port}",
            "",
            'CMD ["npm", "start"]',
        ]
        
        return "\n".join(lines)
    
    @staticmethod
    def _generate_static(path: Path, config: DockerfileConfig) -> str:
        """Generate Dockerfile for static file serving."""
        port = config.port or 80
        
        lines = [
            "FROM nginx:alpine",
            "",
            "COPY . /usr/share/nginx/html",
            "",
            f"EXPOSE {port}",
            "",
            'CMD ["nginx", "-g", "daemon off;"]',
        ]
        
        return "\n".join(lines)
    
    @staticmethod
    def generate_for_app_kernel_service(
        service_path: str,
        shared_libs_path: str,
        port: int = 8000,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Generate Dockerfile specifically for app_kernel services.
        
        Creates structure matching imports:
        /app/
        ├── shared_libs/
        │   └── backend/
        └── services/
            └── {service_name}/
        
        This allows: from shared_libs.backend.xxx import ...
        """
        service_name = Path(service_path).name
        
        lines = [
            "FROM python:3.11-slim",
            "",
            "WORKDIR /app",
            "",
            "# Install system dependencies",
            "RUN apt-get update && apt-get install -y --no-install-recommends \\",
            "    gcc \\",
            "    && rm -rf /var/lib/apt/lists/*",
            "",
        ]
        
        # Check for requirements.txt - REQUIRED for app_kernel services
        req_file = Path(service_path) / "requirements.txt"
        if req_file.exists():
            lines.extend([
                "# Install Python dependencies",
                f"COPY services/{service_name}/requirements.txt ./requirements.txt",
                "RUN pip install --no-cache-dir -r requirements.txt",
                "",
            ])
        else:
            # Minimal fallback - just enough to start uvicorn
            # Service should have requirements.txt for proper deps
            lines.extend([
                "# WARNING: No requirements.txt found - using minimal deps",
                "# Create requirements.txt in your service for proper dependency management",
                "RUN pip install --no-cache-dir fastapi uvicorn[standard]",
                "",
            ])
        
        # Copy shared_libs (with backend inside)
        lines.extend([
            "# Copy shared libraries",
            "COPY shared_libs ./shared_libs",
            "",
            "# Copy service",
            f"COPY services/{service_name} ./services/{service_name}",
            "",
        ])
        
        # Environment variables
        env_vars = env_vars or {}
        if env_vars:
            lines.append("# Environment variables")
            for key, value in env_vars.items():
                lines.append(f"ENV {key}={value}")
            lines.append("")
        
        # Set PYTHONPATH so imports work
        lines.extend([
            "# Set Python path for imports",
            "ENV PYTHONPATH=/app",
            "",
            f"EXPOSE {port}",
            "",
            f'CMD ["uvicorn", "services.{service_name}.main:app", "--host", "0.0.0.0", "--port", "{port}"]',
        ])
        
        return "\n".join(lines)
    
    @staticmethod
    def generate_from_structure(
        files: List[str],
        main_folder: str,
        dep_folders: Optional[List[str]] = None,
        port: int = 8000,
        env_vars: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        Generate Dockerfile from file structure (for API endpoint).
        
        Args:
            files: List of file paths (e.g., ["deploy_api/main.py", "deploy_api/requirements.txt"])
            main_folder: Main service folder name
            dep_folders: Dependency folder names
            port: Port to expose
            env_vars: Environment variables
            
        Returns:
            Dict with "dockerfile", "type", and "source" keys
        """
        dep_folders = dep_folders or []
        
        # Normalize file paths and filter to main folder
        main_files = [f for f in files if f.startswith(f"{main_folder}/") or f == main_folder]
        
        # Detect project type from file structure
        has_requirements = any("requirements.txt" in f for f in main_files)
        has_package_json = any("package.json" in f for f in main_files)
        has_main_py = any("main.py" in f for f in main_files)
        has_app_py = any("app.py" in f for f in main_files)
        has_index_html = any("index.html" in f for f in main_files)
        has_dockerfile = any(f.endswith("Dockerfile") or f.endswith("/Dockerfile") for f in main_files)
        
        # Check for shared_libs dependency
        has_shared_libs = "shared_libs" in dep_folders or any("shared_libs" in f for f in files)
        
        # Determine type
        if has_dockerfile:
            return {
                "dockerfile": "",  # Frontend should use existing
                "type": "existing",
                "source": "from folder",
            }
        
        if has_requirements and (has_main_py or has_app_py):
            # Python FastAPI/Flask style
            project_type = "python-fastapi"
        elif has_package_json:
            project_type = "node"
        elif has_index_html:
            project_type = "static"
        else:
            project_type = "python-generic"
        
        # Generate Dockerfile based on type
        lines = []
        
        if project_type in ("python-fastapi", "python-generic"):
            lines = [
                "FROM python:3.11-slim",
                "",
                "WORKDIR /app",
                "",
            ]
            
            # Add shared_libs if present
            if has_shared_libs:
                lines.extend([
                    "# Copy shared libraries first",
                    "COPY shared_libs/ ./shared_libs/",
                    "",
                ])
            
            # Dependencies
            if has_requirements:
                lines.extend([
                    "# Install dependencies",
                    f"COPY {main_folder}/requirements.txt ./requirements.txt",
                    "RUN pip install --no-cache-dir -r requirements.txt",
                    "",
                ])
            
            # Copy main service
            lines.extend([
                "# Copy application",
                f"COPY {main_folder}/ ./{main_folder}/",
                "",
            ])
            
            # Copy additional dep folders
            for dep in dep_folders:
                if dep != "shared_libs":
                    lines.append(f"COPY {dep}/ ./{dep}/")
            if dep_folders and any(d != "shared_libs" for d in dep_folders):
                lines.append("")
            
            # Env vars
            if env_vars:
                lines.append("# Environment variables")
                for key, value in env_vars.items():
                    lines.append(f"ENV {key}={value}")
                lines.append("")
            
            # Set PYTHONPATH and expose
            lines.extend([
                "ENV PYTHONPATH=/app",
                "",
                f"EXPOSE {port}",
                "",
            ])
            
            # CMD - detect entry point
            if has_main_py:
                lines.append(f'CMD ["uvicorn", "{main_folder}.main:app", "--host", "0.0.0.0", "--port", "{port}"]')
            elif has_app_py:
                lines.append(f'CMD ["uvicorn", "{main_folder}.app:app", "--host", "0.0.0.0", "--port", "{port}"]')
            else:
                lines.append(f'CMD ["python", "-m", "{main_folder}"]')
        
        elif project_type == "node":
            lines = [
                "FROM node:20-slim",
                "",
                "WORKDIR /app",
                "",
                "# Install dependencies",
                f"COPY {main_folder}/package*.json ./",
                "RUN npm ci --only=production",
                "",
                "# Copy application",
                f"COPY {main_folder}/ ./",
                "",
                f"EXPOSE {port}",
                "",
                'CMD ["npm", "start"]',
            ]
        
        elif project_type == "static":
            lines = [
                "FROM nginx:alpine",
                "",
                f"COPY {main_folder}/ /usr/share/nginx/html/",
                "",
                "EXPOSE 80",
                "",
                'CMD ["nginx", "-g", "daemon off;"]',
            ]
        
        return {
            "dockerfile": "\n".join(lines),
            "type": project_type,
            "source": "generated",
        }


# Convenience function
def generate_dockerfile(source_path: str, **kwargs) -> str:
    """Convenience function to generate Dockerfile."""
    config = DockerfileConfig(**kwargs) if kwargs else None
    return DockerfileGenerator.generate(source_path, config)
