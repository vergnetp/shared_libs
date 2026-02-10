"""
Environment validation - fail fast in prod if misconfigured.

In prod (ENV=prod or not set): failures raise and prevent startup.
In non-prod (ENV=dev, uat, staging, test): failures log warnings but allow startup.

Built-in checks validate: database, redis, jwt_secret, cors_origins.
Pass app-specific checks via env_checks parameter in create_service().
"""

import os
import logging
from typing import Callable, List, Tuple, Any

logger = logging.getLogger(__name__)

# Type for check functions: (settings) -> (passed, error_message)
# Settings is a SimpleNamespace with all create_service args
EnvCheck = Callable[[Any], Tuple[bool, str]]

# Non-production environments
NON_PROD_ENVS = {"dev", "uat", "staging", "test", "local", "development"}


def get_env() -> str:
    """Get current environment (APP_ENV or ENV). Defaults to 'prod' if not set."""
    return (
        os.getenv("APP_ENV")
        or os.getenv("ENV")
        or "prod"
    ).lower()

def is_prod() -> bool:
    """Check if running in production."""
    return get_env() not in NON_PROD_ENVS


def is_dev() -> bool:
    """Check if running in development."""
    return get_env() in {"dev", "development", "local"}


def is_uat() -> bool:
    """Check if running in UAT."""
    return get_env() == "uat"


def is_staging() -> bool:
    """Check if running in staging."""
    return get_env() == "staging"


def is_test() -> bool:
    """Check if running in test."""
    return get_env() == "test"


# =============================================================================
# Built-in Checks (run automatically in prod)
# =============================================================================

def check_database_url(settings: Any) -> Tuple[bool, str]:
    """Database must be configured and not SQLite in prod."""
    db_url = getattr(settings, 'database_url', None)
    if not db_url:
        return False, "database_url not set"
    if 'sqlite' in db_url.lower():
        return False, "SQLite not recommended in prod (use PostgreSQL or MySQL)"
    return True, ""


def check_redis_url(settings: Any) -> Tuple[bool, str]:
    """Redis must be configured (not fakeredis) in prod."""
    redis_url = getattr(settings, 'redis_url', None)
    if not redis_url:
        return False, "redis_url not set"
    if 'fakeredis' in redis_url.lower():
        return False, "fakeredis not allowed in prod"
    return True, ""


def check_jwt_secret(settings: Any) -> Tuple[bool, str]:
    """JWT secret must be set and strong."""
    secret = getattr(settings, 'jwt_secret', None)
    if not secret:
        return False, "jwt_secret not set"
    
    weak_secrets = {
        "dev-secret-change-me", "secret", "changeme", 
        "password", "12345", "test", "dev"
    }
    if secret.lower() in weak_secrets:
        return False, "jwt_secret is too weak"
    
    if len(secret) < 32:
        return False, f"jwt_secret too short ({len(secret)} chars, need 32+)"
    
    return True, ""


def check_cors_origins(settings: Any) -> Tuple[bool, str]:
    """CORS origins must be explicitly configured (not wildcard) in prod."""
    origins = getattr(settings, 'cors_origins', None)
    if origins is None:
        return False, "cors_origins not set"
    if origins == ["*"] or "*" in origins:
        return False, "cors_origins cannot be wildcard in prod"
    return True, ""


def check_email_config(settings: Any) -> Tuple[bool, str]:
    """If smtp_url is set, email_from must also be set."""
    smtp_url = getattr(settings, 'smtp_url', None)
    email_from = getattr(settings, 'email_from', None)
    
    if smtp_url and not email_from:
        return False, "email_from required when smtp_url is set"
    if email_from and not smtp_url:
        return False, "smtp_url required when email_from is set"
    
    return True, ""


# All built-in checks
BUILTIN_CHECKS = [
    check_database_url,
    check_redis_url,
    check_jwt_secret,
    check_cors_origins,
    check_email_config,
]


# =============================================================================
# Runner
# =============================================================================

def run_env_checks(
    settings: Any,
    extra_checks: List[EnvCheck] = None,
) -> List[Tuple[str, str]]:
    """
    Run all environment checks.
    
    Args:
        settings: SimpleNamespace with all create_service args
        extra_checks: Additional app-specific checks
    
    Returns:
        List of (check_name, error_message) for failed checks
    
    Raises:
        RuntimeError: In prod if any checks fail
    """
    env = get_env()
    failures: List[Tuple[str, str]] = []
    
    # Only run built-in checks in prod
    checks_to_run = []
    if env == "prod":
        checks_to_run.extend(BUILTIN_CHECKS)
    
    # Always run extra checks (they can check env internally if needed)
    if extra_checks:
        checks_to_run.extend(extra_checks)
    
    for check_func in checks_to_run:
        try:
            passed, error = check_func(settings)
            if not passed:
                check_name = check_func.__name__.replace("check_", "").replace("_", " ").title()
                failures.append((check_name, error))
        except Exception as e:
            check_name = check_func.__name__
            failures.append((check_name, f"Check raised exception: {e}"))
    
    if failures:
        failure_lines = [f"  - {name}: {error}" for name, error in failures]
        failure_msg = "\n".join(failure_lines)
        
        if env == "prod":
            raise RuntimeError(
                f"Environment validation failed in PROD:\n{failure_msg}\n\n"
                f"Fix these issues or set ENV=dev to bypass."
            )
        else:
            logger.warning(f"Environment validation warnings ({env}):\n{failure_msg}")
    else:
        logger.info(f"Environment checks passed ({env})")
    
    return failures
