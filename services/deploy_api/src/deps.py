"""
FastAPI dependencies for deploy_api.

Database connection is managed by app_kernel:
- FastAPI routes: use db_connection dependency (one conn per request)
- Workers/scripts: use get_db_connection() context manager

This module provides app-specific store dependencies.
"""

from fastapi import Depends

# Use kernel's db_connection dependency (handles pooling correctly)
from backend.app_kernel.db import db_connection

from .stores import (
    WorkspaceStore,
    ProjectStore,
    CredentialsStore,
    DeploymentStore,
    DatabaseStorageAdapter,
)


# =============================================================================
# Store Dependencies (FastAPI - share kernel's connection)
# =============================================================================

async def get_workspace_store(db = Depends(db_connection)):
    """Get workspace store - shares request connection."""
    return WorkspaceStore(db)


async def get_project_store(db = Depends(db_connection)):
    """Get project store - shares request connection."""
    return ProjectStore(db)


async def get_credentials_store(db = Depends(db_connection)):
    """Get credentials store - shares request connection."""
    return CredentialsStore(db)


async def get_deployment_store(db = Depends(db_connection)):
    """Get deployment store - shares request connection."""
    return DeploymentStore(db)


# =============================================================================
# Storage Adapter (for infra compatibility)
# =============================================================================

async def get_storage_adapter(db = Depends(db_connection)) -> DatabaseStorageAdapter:
    """Get storage adapter for infra compatibility."""
    project_store = ProjectStore(db)
    deployment_store = DeploymentStore(db)
    return DatabaseStorageAdapter(project_store, deployment_store)
