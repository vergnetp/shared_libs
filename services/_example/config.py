"""
Application configuration.

This file is generated ONCE - safe to customize after creation.
"""

import os
from pathlib import Path
from dataclasses import dataclass

# Service directory (where this file lives)
SERVICE_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Settings:
    """Application settings from environment."""
    
    # Database
    database_path: str = str(SERVICE_DIR / "data/example_app.db")
    database_type: str = "sqlite"
    
    # Redis
    redis_url: str = os.environ["REDIS_URL"]
    
    # Auth
    jwt_secret: str = os.environ["JWT_SECRET"]
    
    @property
    def database_name(self) -> str:
        """Extract database name from path."""
        return Path(self.database_path).stem
    
    def ensure_data_dir(self):
        """Create data directory if needed."""
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
