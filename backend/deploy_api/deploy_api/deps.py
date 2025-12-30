"""
Dependency injection for deploy API.

Database is managed by kernel. This module provides app-specific stores.
"""
from typing import AsyncGenerator
from contextlib import asynccontextmanager

from backend.app_kernel.db import get_db_session, get_db_manager, db_session_dependency

from .stores import (
    WorkspaceStore,
    ProjectStore,
    CredentialsStore,
    DeploymentStore,
    DatabaseStorageAdapter,
)


# =============================================================================
# Database Connection (from kernel)
# =============================================================================

# Re-export for convenience
get_db = get_db_session


async def get_db_dependency():
    """FastAPI dependency for database connection."""
    async with get_db_session() as conn:
        yield conn


# =============================================================================
# Store Dependencies (per-request)
# =============================================================================

async def get_workspace_store():
    """Get workspace store - FastAPI dependency."""
    async with get_db_session() as conn:
        yield WorkspaceStore(conn)


async def get_project_store():
    """Get project store - FastAPI dependency."""
    async with get_db_session() as conn:
        yield ProjectStore(conn)


async def get_credentials_store():
    """Get credentials store - FastAPI dependency."""
    async with get_db_session() as conn:
        yield CredentialsStore(conn)


async def get_deployment_store():
    """Get deployment store - FastAPI dependency."""
    async with get_db_session() as conn:
        yield DeploymentStore(conn)


# =============================================================================
# Sync access for workers
# =============================================================================

def get_db_sync():
    """Get database for sync context (workers)."""
    db_manager = get_db_manager()
    return db_manager.__enter__()


def release_db_sync():
    """Release sync connection."""
    db_manager = get_db_manager()
    db_manager.__exit__(None, None, None)


def get_credentials_store_sync(conn) -> CredentialsStore:
    """Get credentials store synchronously (for workers)."""
    return CredentialsStore(conn)


# =============================================================================
# Storage Adapter (for infra compatibility)
# =============================================================================

async def get_storage_adapter() -> DatabaseStorageAdapter:
    """Get storage adapter for infra compatibility."""
    async with get_db_session() as conn:
        project_store = ProjectStore(conn)
        deployment_store = DeploymentStore(conn)
        return DatabaseStorageAdapter(project_store, deployment_store)
