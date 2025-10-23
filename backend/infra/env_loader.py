# env_loader.py
import os
from pathlib import Path
from dotenv import load_dotenv

def load_env():
    """Load .env file into os.environ - idempotent"""
    if os.getenv("_ENV_LOADED"):  # Skip if already loaded
        return
    
    try:        
        load_dotenv()
    except ImportError:
        env_file = Path('.env')
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()
    
    os.environ["_ENV_LOADED"] = "1"

# Auto-load on import
load_env()