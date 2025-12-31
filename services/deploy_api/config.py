"""
Configuration settings for deploy_api.

This file is generated ONCE - safe to customize after creation.
"""

import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional


# Service directory (where this file lives)
SERVICE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    """Deploy API settings from environment."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="DEPLOY_",
        extra="ignore",
    )
    
    # Service info
    service_name: str = "deploy-api"
    service_version: str = "0.1.0"
    debug: bool = False
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    
    # Database
    database_path: str = str(SERVICE_DIR / "data" / "deploy.db")
    database_type: str = "sqlite"
    database_host: str = "localhost"
    database_port: Optional[int] = None
    database_user: Optional[str] = None
    database_password: Optional[str] = None
    
    # Redis (for job queue) - uses REDIS_URL without prefix
    redis_url: Optional[str] = Field(
        default="redis://localhost:6379",
        validation_alias="REDIS_URL",
    )
    redis_key_prefix: str = "queue:"  # Match job_queue default
    
    # Auth - JWT_SECRET without prefix for compatibility
    jwt_secret: str = Field(
        default="change-me-in-production",
        validation_alias="JWT_SECRET",
    )
    jwt_expiry_hours: int = 24
    auth_enabled: bool = True
    allow_self_signup: bool = True  # For initial setup
    
    # CORS
    cors_origins: list[str] = ["*"]
    
    # Encryption key for credentials
    encryption_key: Optional[str] = None
    
    @property
    def database_name(self) -> str:
        """Extract database name from path."""
        return Path(self.database_path).stem
    
    def ensure_data_dir(self):
        """Create data directory if needed."""
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
