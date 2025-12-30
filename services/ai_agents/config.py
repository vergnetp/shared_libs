"""
Service configuration using environment variables.

All settings can be overridden with AGENT_ prefixed env vars.
Settings are frozen after creation (no runtime mutation).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, List
from functools import lru_cache

from dotenv import load_dotenv
load_dotenv()


def _env(key: str, default: str = None) -> Optional[str]:
    """Get env var with AGENT_ prefix."""
    return os.environ.get(f"AGENT_{key}", os.environ.get(key, default))


def _env_bool(key: str, default: bool = False) -> bool:
    """Get boolean env var."""
    val = _env(key)
    if val is None:
        return default
    return val.lower() in ("true", "1", "yes")


def _env_int(key: str, default: int) -> int:
    """Get integer env var."""
    val = _env(key)
    return int(val) if val else default


def _env_float(key: str, default: float) -> float:
    """Get float env var."""
    val = _env(key)
    return float(val) if val else default


@dataclass(frozen=True)
class Settings:
    """
    Application settings - frozen after creation.
    
    No per-request or runtime mutation allowed.
    """
    # Service
    service_name: str
    service_version: str
    debug: bool
    host: str
    port: int
    
    # Database
    database_type: str
    database_name: str
    database_host: str
    database_port: int
    database_user: Optional[str]
    database_password: Optional[str]
    
    # Redis
    redis_url: Optional[str]
    
    # AI Providers
    openai_api_key: Optional[str]
    anthropic_api_key: Optional[str]
    groq_api_key: Optional[str]
    ollama_base_url: str
    default_provider: str
    default_model: str
    
    # Budgets
    conversation_budget: float
    total_budget: float
    
    # Embeddings
    embedding_model: Optional[str]
    
    # Storage
    upload_dir: str
    max_upload_size: int
    
    # Auth
    auth_enabled: bool
    auth_store: str
    jwt_secret: str
    jwt_algorithm: str
    jwt_expiry_hours: int
    allow_self_signup: bool
    
    # Rate limiting
    rate_limit_requests: int
    rate_limit_window: int
    concurrent_streams: int
    stream_lease_ttl: int
    
    # CORS
    cors_origins: tuple  # Use tuple for frozen dataclass
    
    # Convenience properties
    @property
    def db_type(self) -> str:
        return self.database_type
    
    @property
    def db_path(self) -> str:
        return self.database_name


def _create_settings() -> Settings:
    """Create settings from environment."""
    cors = _env("CORS_ORIGINS", "*")
    cors_list = tuple(cors.split(",")) if cors else ("*",)
    
    return Settings(
        # Service
        service_name=_env("SERVICE_NAME", "agent-service"),
        service_version=_env("SERVICE_VERSION", "0.2.0"),
        debug=_env_bool("DEBUG", False),
        host=_env("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8000),
        
        # Database
        database_type=_env("DATABASE_TYPE", "sqlite"),
        database_name=_env("DATABASE_NAME", "./data/agents.db"),
        database_host=_env("DATABASE_HOST", "localhost"),
        database_port=_env_int("DATABASE_PORT", 5432),
        database_user=_env("DATABASE_USER"),
        database_password=_env("DATABASE_PASSWORD"),
        
        # Redis
        redis_url=_env("REDIS_URL"),
        
        # AI Providers
        openai_api_key=_env("OPENAI_API_KEY"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        groq_api_key=_env("GROQ_API_KEY"),
        ollama_base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434"),
        default_provider=_env("DEFAULT_PROVIDER", "anthropic"),
        default_model=_env("DEFAULT_MODEL", "claude-sonnet-4-20250514"),
        
        # Budgets
        conversation_budget=_env_float("CONVERSATION_BUDGET", 1.0),
        total_budget=_env_float("TOTAL_BUDGET", 100.0),
        
        # Embeddings
        embedding_model=_env("EMBEDDING_MODEL"),
        
        # Storage
        upload_dir=_env("UPLOAD_DIR", "./data/uploads"),
        max_upload_size=_env_int("MAX_UPLOAD_SIZE", 10 * 1024 * 1024),
        
        # Auth
        auth_enabled=_env_bool("AUTH_ENABLED", False),
        auth_store=_env("AUTH_STORE", "database"),
        jwt_secret=_env("JWT_SECRET", "change-me-in-production"),
        jwt_algorithm=_env("JWT_ALGORITHM", "HS256"),
        jwt_expiry_hours=_env_int("JWT_EXPIRY_HOURS", 24),
        allow_self_signup=_env_bool("ALLOW_SELF_SIGNUP", False),
        
        # Rate limiting
        rate_limit_requests=_env_int("RATE_LIMIT_REQUESTS", 100),
        rate_limit_window=_env_int("RATE_LIMIT_WINDOW", 60),
        concurrent_streams=_env_int("MAX_CONCURRENT_STREAMS", 3),
        stream_lease_ttl=_env_int("STREAM_LEASE_TTL", 360),
        
        # CORS
        cors_origins=cors_list,
    )


@lru_cache()
def get_settings() -> Settings:
    """Get settings instance (cached, frozen)."""
    return _create_settings()
