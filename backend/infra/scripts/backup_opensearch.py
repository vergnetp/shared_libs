#!/usr/bin/env python3
"""
OpenSearch Backup Script with Verification

Runs as a scheduled container to backup OpenSearch indices.
Connects to parent OpenSearch container via Docker DNS.
Creates snapshot and verifies integrity.

IMPORTANT: This script does NOT import ResourceResolver or any infra libraries.
It relies purely on environment variables injected by BackupManager.
"""

import os
import subprocess
import sys
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep

# Environment variables (inherited from parent opensearch config)
HOST = os.environ.get("HOST", "localhost")  # Container name
OPENSEARCH_PORT = os.environ.get("OPENSEARCH_PORT", "9200")

# CRITICAL: Generic PASSWORD_FILE injected by BackupManager
# This is the same for all services - no need for OPENSEARCH_PASSWORD_FILE
PASSWORD_FILE = os.environ.get("PASSWORD_FILE", "/run/secrets/opensearch_password")

# Fallback for backward compatibility with old deployments
OPENSEARCH_PASSWORD_FILE = os.environ.get("OPENSEARCH_PASSWORD_FILE", PASSWORD_FILE)

# Admin user (OpenSearch default)
OPENSEARCH_USER = os.environ.get("OPENSEARCH_USER", "admin")

# Backup-specific env vars
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 7))
SERVICE_NAME = os.environ.get("SERVICE_NAME", "opensearch")
PROJECT = os.environ.get("PROJECT", "unknown")
ENV = os.environ.get("ENV", "unknown")

# Snapshot repository settings
REPO_NAME = f"{PROJECT}_{ENV}_backup_repo"
SNAPSHOT_PATH = "/backups/snapshots"


def get_password():
    """Read password from mounted secret file"""
    password_file = Path(OPENSEARCH_PASSWORD_FILE)
    if not password_file.exists():
        raise FileNotFoundError(
            f"Password file not found: {OPENSEARCH_PASSWORD_FILE}\n"
            f"Ensure the secrets volume is mounted and PASSWORD_FILE env var is set correctly."
        )
    return password_file.read_text().strip()


def make_request(method: str, endpoint: str, json_data: dict = None, timeout: int = 30):
    """Make authenticated request to OpenSearch API"""
    password = get_password()
    url = f"https://{HOST}:{OPENSEARCH_PORT}{endpoint}"
    
    try:
        response = requests.request(
            method,
            url,
            auth=(OPENSEARCH_USER, password),
            json=json_data,
            verify=False,  # Self-signed cert
            timeout=timeout
        )
        response.raise_for_status()
        return response.json() if response.text else {}
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        raise


def wait_for_opensearch(max_retries: int = 30, delay: int = 2):
    """Wait for OpenSearch to be ready"""
    print(f"Waiting for OpenSearch at {HOST}:{OPENSEARCH_PORT}...")
    
    for i in range(max_retries):
        try:
            response = make_request("GET", "/_cluster/health")
            status = response.get("status")
            print(f"✓ OpenSearch is ready (status: {status})")
            return True
        except Exception as e:
            if i < max_retries - 1:
                print(f"  Attempt {i+1}/{max_retries}: Not ready yet, waiting {delay}s...")
                sleep(delay)
            else:
                print(f"✗ OpenSearch not ready after {max_retries} attempts")
                return False
    
    return False


def register_snapshot_repository():
    """Register or update snapshot repository"""
    print(f"\nRegistering snapshot repository: {REPO_NAME}")
    
    repo_config = {
        "type": "fs",
        "settings": {
            "location": SNAPSHOT_PATH,
            "compress": True
        }
    }
    
    try:
        make_request("PUT", f"/_snapshot/{REPO_NAME}", repo_config)
        print(f"✓ Repository registered: {REPO_NAME}")
        return True
    except Exception as e:
        print(f"✗ Failed to register repository: {e}")
        return False


def create_snapshot(snapshot_name: str):
    """Create a snapshot of all indices"""
    print(f"\nCreating snapshot: {snapshot_name}")
    
    snapshot_config = {
        "indices": "*",
        "ignore_unavailable": True,
        "include_global_state": True
    }
    
    try:
        # Trigger snapshot creation
        make_request(
            "PUT",
            f"/_snapshot/{REPO_NAME}/{snapshot_name}?wait_for_completion=false",
            snapshot_config
        )
        print(f"  Snapshot creation initiated...")
        
        # Poll for completion
        max_wait = 300  # 5 minutes
        interval = 5
        elapsed = 0
        
        while elapsed < max_wait:
            sleep(interval)
            elapsed += interval
            
            status = make_request("GET", f"/_snapshot/{REPO_NAME}/{snapshot_name}")
            
            if status.get("snapshots"):
                snapshot_status = status["snapshots"][0]
                state = snapshot_status.get("state")
                
                if state == "SUCCESS":
                    shards = snapshot_status.get("shards", {})
                    print(f"✓ Snapshot completed successfully")
                    print(f"  Total shards: {shards.get('total', 0)}")
                    print(f"  Successful shards: {shards.get('successful', 0)}")
                    print(f"  Failed shards: {shards.get('failed', 0)}")
                    return True
                elif state == "FAILED":
                    print(f"✗ Snapshot failed")
                    print(f"  Failures: {snapshot_status.get('failures', [])}")
                    return False
                elif state == "IN_PROGRESS":
                    print(f"  Still in progress... ({elapsed}s elapsed)")
                else:
                    print(f"  Unknown state: {state}")
        
        print(f"✗ Snapshot timed out after {max_wait}s")
        return False
        
    except Exception as e:
        print(f"✗ Snapshot creation failed: {e}")
        return False


def verify_snapshot(snapshot_name: str):
    """Verify snapshot integrity"""
    print(f"\nVerifying snapshot: {snapshot_name}")
    
    try:
        # Get snapshot details
        response = make_request("GET", f"/_snapshot/{REPO_NAME}/{snapshot_name}")
        
        if not response.get("snapshots"):
            print(f"✗ Snapshot not found")
            return False
        
        snapshot = response["snapshots"][0]
        
        # Check state
        if snapshot.get("state") != "SUCCESS":
            print(f"✗ Snapshot state is not SUCCESS: {snapshot.get('state')}")
            return False
        
        # Check shards
        shards = snapshot.get("shards", {})
        total = shards.get("total", 0)
        successful = shards.get("successful", 0)
        failed = shards.get("failed", 0)
        
        if failed > 0:
            print(f"✗ Snapshot has {failed} failed shards")
            return False
        
        if successful != total:
            print(f"✗ Not all shards backed up successfully ({successful}/{total})")
            return False
        
        # Check indices
        indices = snapshot.get("indices", [])
        print(f"✓ Snapshot verified successfully")
        print(f"  Indices backed up: {len(indices)}")
        print(f"  Shards: {successful}/{total} successful")
        
        return True
        
    except Exception as e:
        print(f"✗ Snapshot verification failed: {e}")
        return False


def backup():
    """Create and verify an OpenSearch snapshot"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_name = f"snapshot_{timestamp}"
    
    print("=" * 60)
    print(f"OpenSearch Backup Starting")
    print("=" * 60)
    print(f"  Project: {PROJECT}")
    print(f"  Environment: {ENV}")
    print(f"  Service: {SERVICE_NAME}")
    print(f"  Timestamp: {timestamp}")
    print(f"  Host: {HOST}:{OPENSEARCH_PORT}")
    print(f"  User: {OPENSEARCH_USER}")
    print(f"  Password File: {OPENSEARCH_PASSWORD_FILE}")
    print(f"  Repository: {REPO_NAME}")
    print(f"  Snapshot: {snapshot_name}")
    print()
    
    # Wait for OpenSearch to be ready
    if not wait_for_opensearch():
        print("ERROR: OpenSearch is not ready")
        sys.exit(1)
    
    # Register repository (idempotent)
    if not register_snapshot_repository():
        print("ERROR: Failed to register snapshot repository")
        sys.exit(1)
    
    # Create snapshot
    if not create_snapshot(snapshot_name):
        print("ERROR: Snapshot creation failed")
        sys.exit(1)
    
    # Verify snapshot
    if not verify_snapshot(snapshot_name):
        print("ERROR: Snapshot verification failed")
        sys.exit(1)
    
    print()
    print("=" * 60)
    print("✓ Backup completed successfully")
    print("=" * 60)


def cleanup_old_snapshots():
    """Remove snapshots older than RETENTION_DAYS"""
    print()
    print(f"Cleaning up snapshots older than {RETENTION_DAYS} days...")
    
    try:
        # Get all snapshots
        response = make_request("GET", f"/_snapshot/{REPO_NAME}/_all")
        snapshots = response.get("snapshots", [])
        
        if not snapshots:
            print("  No snapshots to clean up")
            return
        
        cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
        removed_count = 0
        
        for snapshot in snapshots:
            snapshot_name = snapshot.get("snapshot")
            
            # Extract timestamp from snapshot name
            try:
                timestamp_str = snapshot_name.replace("snapshot_", "")
                snapshot_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
                
                if snapshot_date < cutoff_date:
                    print(f"  Removing: {snapshot_name}")
                    make_request("DELETE", f"/_snapshot/{REPO_NAME}/{snapshot_name}")
                    removed_count += 1
            except Exception as e:
                print(f"  Warning: Could not process {snapshot_name}: {e}")
        
        if removed_count > 0:
            print(f"✓ Removed {removed_count} old snapshot(s)")
        else:
            print("  No old snapshots to remove")
            
    except Exception as e:
        print(f"Warning: Cleanup failed: {e}")


if __name__ == "__main__":
    # Suppress SSL warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    try:
        backup()
        cleanup_old_snapshots()
    except KeyboardInterrupt:
        print("\nBackup interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)