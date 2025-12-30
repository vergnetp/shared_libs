"""
Configuration settings for deploy API.
"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Deploy API settings."""
    
    # Service info
    service_name: str = "deploy-api"
    service_version: str = "0.1.0"
    debug: bool = False
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    
    # Database
    database_name: str = "./data/deploy.db"
    database_type: str = "sqlite"  # sqlite, postgres, mysql
    database_host: str = "localhost"
    database_port: Optional[int] = None  # None = use default for type
    database_user: Optional[str] = None
    database_password: Optional[str] = None
    
    # Redis (for job queue)
    redis_url: Optional[str] = None
    
    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_expiry_hours: int = 24
    auth_enabled: bool = True
    allow_self_signup: bool = True  # For initial setup
    
    # CORS
    cors_origins: list[str] = ["*"]
    
    # Encryption key for credentials
    encryption_key: Optional[str] = None
    
    # Infra paths (can override defaults)
    infra_config_path: Optional[str] = None
    infra_local_path: Optional[str] = None
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
