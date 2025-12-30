"""
App Manifest Schema - defines the structure of app.manifest.yaml

This is the contract between:
- appctl (scaffold generator)
- deploy_api (deployment automation)
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Literal
import yaml


@dataclass
class DatabaseConfig:
    """Database configuration."""
    type: Literal["sqlite", "postgres", "mysql"] = "sqlite"
    name: str = "./data/app.db"  # DB name or file path
    host: str = "localhost"
    port: Optional[int] = None   # None = use default for type
    user: Optional[str] = None
    password_env: Optional[str] = None  # Env var name for password


@dataclass
class RedisConfig:
    """Redis configuration."""
    enabled: bool = False
    url_env: str = "REDIS_URL"  # Env var name for URL
    key_prefix: Optional[str] = None  # Auto-generated from app name if None


@dataclass
class AuthConfig:
    """Authentication configuration."""
    enabled: bool = True
    allow_signup: bool = False
    jwt_secret_env: str = "JWT_SECRET"
    jwt_expiry_hours: int = 24


@dataclass
class CorsConfig:
    """CORS configuration."""
    origins: List[str] = field(default_factory=lambda: ["*"])
    credentials: bool = True


@dataclass
class EntityField:
    """Field definition for an entity."""
    name: str
    type: Literal["string", "text", "int", "float", "bool", "datetime", "json"] = "string"
    required: bool = True
    default: Optional[Any] = None


@dataclass
class EntityConfig:
    """Entity definition - generates schema + optional CRUD routes."""
    name: str
    fields: List[EntityField] = field(default_factory=list)
    workspace_scoped: bool = True  # If True, adds workspace_id column
    generate_routes: bool = True   # If True, generates CRUD routes
    soft_delete: bool = True       # If True, adds deleted_at column


@dataclass 
class AppManifest:
    """
    Complete app manifest.
    
    Usage:
        manifest = AppManifest.from_yaml("app.manifest.yaml")
        # or
        manifest = AppManifest(
            name="my-service",
            database=DatabaseConfig(type="postgres"),
            redis=RedisConfig(enabled=True),
            tasks=["process_order", "send_email"],
        )
    """
    # Required
    name: str
    
    # Optional with defaults
    version: str = "1.0.0"
    description: str = ""
    
    # Infrastructure
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    
    # Features
    auth: AuthConfig = field(default_factory=AuthConfig)
    cors: CorsConfig = field(default_factory=CorsConfig)
    
    # Background tasks (just names - handlers go in tasks.py)
    tasks: List[str] = field(default_factory=list)
    
    # Entity definitions (generates schema + routes)
    entities: List[EntityConfig] = field(default_factory=list)
    
    # API settings
    api_prefix: str = "/api/v1"
    
    # Runtime
    host: str = "0.0.0.0"
    port: int = 8000
    debug_env: str = "DEBUG"  # Env var to check for debug mode
    
    @classmethod
    def from_yaml(cls, path: str) -> "AppManifest":
        """Load manifest from YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)
    
    @classmethod
    def from_dict(cls, data: dict) -> "AppManifest":
        """Create manifest from dictionary."""
        if not isinstance(data, dict):
            raise ValueError(f"Expected dict, got {type(data)}")
        
        if "name" not in data:
            raise ValueError("Manifest must have 'name' field")
        
        # Parse nested configs with error handling
        try:
            db_data = data.get("database", {})
            if isinstance(db_data, dict):
                database = DatabaseConfig(**db_data)
            else:
                database = DatabaseConfig()
        except Exception as e:
            print(f"Warning: Error parsing database config: {e}")
            database = DatabaseConfig()
        
        try:
            redis_data = data.get("redis", {})
            if isinstance(redis_data, bool):
                redis = RedisConfig(enabled=redis_data)
            elif isinstance(redis_data, dict):
                redis = RedisConfig(**redis_data)
            else:
                redis = RedisConfig()
        except Exception as e:
            print(f"Warning: Error parsing redis config: {e}")
            redis = RedisConfig()
        
        try:
            auth_data = data.get("auth", {})
            if isinstance(auth_data, bool):
                auth = AuthConfig(enabled=auth_data)
            elif isinstance(auth_data, dict):
                auth = AuthConfig(**auth_data)
            else:
                auth = AuthConfig()
        except Exception as e:
            print(f"Warning: Error parsing auth config: {e}")
            auth = AuthConfig()
        
        try:
            cors_data = data.get("cors", {})
            if isinstance(cors_data, dict):
                cors = CorsConfig(**cors_data)
            else:
                cors = CorsConfig()
        except Exception as e:
            print(f"Warning: Error parsing cors config: {e}")
            cors = CorsConfig()
        
        # Parse entities
        entities = []
        for entity_data in data.get("entities", []) or []:
            try:
                fields = []
                for field_data in entity_data.get("fields", []) or []:
                    if isinstance(field_data, dict):
                        # Format: {name: "title", type: "string"}
                        fields.append(EntityField(**field_data))
                    elif isinstance(field_data, str):
                        # Format: "title" (assumes string type)
                        fields.append(EntityField(name=field_data))
                
                entity = EntityConfig(
                    name=entity_data["name"],
                    fields=fields,
                    workspace_scoped=entity_data.get("workspace_scoped", True),
                    generate_routes=entity_data.get("generate_routes", True),
                    soft_delete=entity_data.get("soft_delete", True),
                )
                entities.append(entity)
            except Exception as e:
                print(f"Warning: Error parsing entity: {e}")
        
        result = cls(
            name=data["name"],
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            database=database,
            redis=redis,
            auth=auth,
            cors=cors,
            tasks=data.get("tasks", []) or [],
            entities=entities,
            api_prefix=data.get("api_prefix", "/api/v1"),
            host=data.get("host", "0.0.0.0"),
            port=data.get("port", 8000),
            debug_env=data.get("debug_env", "DEBUG"),
        )
        
        # Validate that nested configs are correct types
        if not isinstance(result.database, DatabaseConfig):
            raise TypeError(f"database should be DatabaseConfig, got {type(result.database)}")
        if not isinstance(result.redis, RedisConfig):
            raise TypeError(f"redis should be RedisConfig, got {type(result.redis)}")
        if not isinstance(result.auth, AuthConfig):
            raise TypeError(f"auth should be AuthConfig, got {type(result.auth)}")
        
        return result
    
    def to_dict(self) -> dict:
        """Convert manifest to dictionary (for YAML output)."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "database": {
                "type": self.database.type,
                "name": self.database.name,
                "host": self.database.host,
                "port": self.database.port,
                "user": self.database.user,
                "password_env": self.database.password_env,
            },
            "redis": {
                "enabled": self.redis.enabled,
                "url_env": self.redis.url_env,
                "key_prefix": self.redis.key_prefix,
            },
            "auth": {
                "enabled": self.auth.enabled,
                "allow_signup": self.auth.allow_signup,
                "jwt_secret_env": self.auth.jwt_secret_env,
                "jwt_expiry_hours": self.auth.jwt_expiry_hours,
            },
            "cors": {
                "origins": self.cors.origins,
                "credentials": self.cors.credentials,
            },
            "tasks": self.tasks,
            "entities": [
                {
                    "name": e.name,
                    "fields": [{"name": f.name, "type": f.type} for f in e.fields],
                    "workspace_scoped": e.workspace_scoped,
                    "generate_routes": e.generate_routes,
                    "soft_delete": e.soft_delete,
                }
                for e in self.entities
            ],
            "api_prefix": self.api_prefix,
            "host": self.host,
            "port": self.port,
        }
    
    def to_yaml(self, path: str = None) -> str:
        """Convert to YAML string, optionally write to file."""
        content = yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)
        if path:
            with open(path, "w") as f:
                f.write(content)
        return content
