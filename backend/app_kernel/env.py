"""
Environment loading with hierarchy support.

.env hierarchy (highest priority first):
  1. System environment variables  (always win)
  2. {service}/.env                (service-specific)
  3. {services}/.env               (all services)  
  4. {root}/.env                   (shared defaults)

Usage:
    from app_kernel.env import load_env_hierarchy
    
    # In your service's config.py or __init__.py:
    load_env_hierarchy(__file__)  # Pass any file in your service dir
    
    # Or with explicit paths:
    load_env_hierarchy(
        service_dir="/path/to/services/my_service",
        root_dir="/path/to/shared_libs",
    )
"""

import os
from pathlib import Path
from typing import Optional, List


def load_env_hierarchy(
    service_file: Optional[str] = None,
    service_dir: Optional[str] = None,
    root_dir: Optional[str] = None,
    extra_env_files: Optional[List[str]] = None,
) -> List[Path]:
    """
    Load .env files with correct priority (system env vars always win).
    
    Priority (highest to lowest):
      1. System environment variables (already in os.environ)
      2. Service-specific .env (most specific)
      3. Services directory .env
      4. Root .env (least specific defaults)
    
    Args:
        service_file: Any file in the service directory (e.g., __file__)
        service_dir: Explicit service directory path
        root_dir: Explicit root directory (defaults to grandparent of service_dir)
        extra_env_files: Additional .env files to load (highest priority after system)
    
    Returns:
        List of .env files that were loaded
        
    Example:
        # In services/deploy_api/config.py:
        load_env_hierarchy(__file__)
        
        # System env vars win, then checks (in order):
        #   services/deploy_api/.env
        #   services/.env
        #   shared_libs/.env
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        import logging
        logging.getLogger(__name__).warning(
            "python-dotenv not installed - .env files will not be loaded. "
            "Install with: pip install python-dotenv"
        )
        return []
    
    # Determine service directory
    if service_dir:
        svc_dir = Path(service_dir)
    elif service_file:
        svc_dir = Path(service_file).parent
    else:
        # Fallback: current working directory
        svc_dir = Path.cwd()
    
    svc_dir = svc_dir.resolve()
    
    # Determine root directory (typically shared_libs/)
    # Assumes structure: root/services/service_name/
    if root_dir:
        root = Path(root_dir).resolve()
    else:
        # Grandparent of service dir
        root = svc_dir.parent.parent
    
    # Services directory (parent of service dir)
    services_dir = svc_dir.parent
    
    # Build list of .env files (general → specific)
    env_files = [
        root / ".env",           # shared_libs/.env
        services_dir / ".env",   # services/.env
        svc_dir / ".env",        # services/my_service/.env
    ]
    
    # Add any extra files
    if extra_env_files:
        env_files.extend(Path(f) for f in extra_env_files)
    
    # Load in REVERSE order with override=False
    # This ensures:
    #   1. System env vars always win (already in os.environ)
    #   2. Most specific .env file wins over less specific ones
    #   3. Root .env only provides defaults for unset vars
    loaded = []
    for env_file in reversed(env_files):
        if env_file.exists():
            load_dotenv(env_file, override=False)
            loaded.append(env_file)
    
    # Return in original order for clarity
    return list(reversed(loaded))


def get_env(key: str, default: str = None, required: bool = False) -> Optional[str]:
    """
    Get environment variable with optional requirement check.
    
    Args:
        key: Environment variable name
        default: Default value if not set
        required: If True, raise ValueError when not set
    
    Returns:
        Environment variable value or default
        
    Raises:
        ValueError: If required=True and variable is not set
    """
    value = os.environ.get(key, default)
    
    if required and value is None:
        raise ValueError(f"Required environment variable {key} is not set")
    
    return value


def get_env_bool(key: str, default: bool = False) -> bool:
    """Get environment variable as boolean."""
    value = os.environ.get(key, "").lower()
    if value in ("true", "1", "yes", "on"):
        return True
    elif value in ("false", "0", "no", "off"):
        return False
    return default


def get_env_int(key: str, default: int = 0) -> int:
    """Get environment variable as integer."""
    value = os.environ.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_env_list(key: str, default: List[str] = None, separator: str = ",") -> List[str]:
    """Get environment variable as list."""
    value = os.environ.get(key)
    if value is None:
        return default or []
    return [item.strip() for item in value.split(separator) if item.strip()]
