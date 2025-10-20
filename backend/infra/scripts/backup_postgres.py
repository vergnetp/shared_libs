#!/usr/bin/env python3
"""
Postgres Backup Script with Verification

Runs as a scheduled container to backup Postgres database.
Connects to parent Postgres container via Docker DNS.
Verifies backup integrity after creation.

IMPORTANT: This script does NOT import ResourceResolver or any infra libraries.
It relies purely on environment variables injected by BackupManager.
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Environment variables (inherited from parent postgres config)
HOST = os.environ.get("HOST", "localhost")  # Container name
POSTGRES_DB = os.environ.get("POSTGRES_DB")
POSTGRES_USER = os.environ.get("POSTGRES_USER")

# CRITICAL: Generic PASSWORD_FILE injected by BackupManager
# This is the same for all services - no need for POSTGRES_PASSWORD_FILE
PASSWORD_FILE = os.environ.get("PASSWORD_FILE", "/run/secrets/postgres_password")

# Fallback for backward compatibility with old deployments
POSTGRES_PASSWORD_FILE = os.environ.get("POSTGRES_PASSWORD_FILE", PASSWORD_FILE)

# Backup-specific env vars
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 7))
SERVICE_NAME = os.environ.get("SERVICE_NAME", "postgres")
PROJECT = os.environ.get("PROJECT", "unknown")
ENV = os.environ.get("ENV", "unknown")


def get_db_password():
    """Read password from mounted secret file"""
    password_file = Path(POSTGRES_PASSWORD_FILE)
    if not password_file.exists():
        raise FileNotFoundError(
            f"Password file not found: {POSTGRES_PASSWORD_FILE}\n"
            f"Ensure the secrets volume is mounted and PASSWORD_FILE env var is set correctly."
        )
    return password_file.read_text().strip()


def verify_backup(backup_file: str, password: str) -> bool:
    """
    Verify backup integrity by listing its contents.
    
    Uses pg_restore --list to check if the backup is valid
    without actually restoring it.
    
    Args:
        backup_file: Path to backup file
        password: Database password
        
    Returns:
        True if backup is valid
    """
    print(f"Verifying backup integrity...")
    
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    
    # Use pg_restore --list to validate without restoring
    cmd = [
        "pg_restore",
        "--list",
        backup_file
    ]
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            env=env,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # Check that we got some output (list of objects)
        if result.stdout and len(result.stdout) > 10:
            # Count database objects in the backup
            lines = result.stdout.strip().split('\n')
            object_count = sum(1 for line in lines if line and not line.startswith(';'))
            
            print(f"✓ Backup verified: {object_count} database objects found")
            return True
        else:
            print(f"✗ Backup verification failed: No objects found in backup")
            return False
            
    except subprocess.TimeoutExpired:
        print(f"✗ Backup verification timed out")
        return False
    except subprocess.CalledProcessError as e:
        print(f"✗ Backup verification failed:")
        print(f"  Return code: {e.returncode}")
        if e.stderr:
            print(f"  Error: {e.stderr}")
        return False


def backup():
    """Create a compressed backup of the Postgres database"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"/backups/postgres_{timestamp}.dump"
    
    print("=" * 60)
    print(f"Postgres Backup Starting")
    print("=" * 60)
    print(f"  Project: {PROJECT}")
    print(f"  Environment: {ENV}")
    print(f"  Service: {SERVICE_NAME}")
    print(f"  Timestamp: {timestamp}")
    print(f"  Host: {HOST}")
    print(f"  Database: {POSTGRES_DB}")
    print(f"  User: {POSTGRES_USER}")
    print(f"  Password File: {POSTGRES_PASSWORD_FILE}")
    print(f"  Output: {backup_file}")
    print()
    
    # Get password
    try:
        password = get_db_password()
        print("✓ Password file found and read successfully")
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
    
    # Set password in environment for pg_dump
    env = os.environ.copy()
    env["PGPASSWORD"] = password
    
    # Run pg_dump
    cmd = [
        "pg_dump",
        "-h", HOST,
        "-U", POSTGRES_USER,
        "-d", POSTGRES_DB,
        "-Fc",  # Custom compressed format
        "-f", backup_file,
        "--verbose"
    ]
    
    print("Creating backup...")
    try:
        result = subprocess.run(
            cmd,
            check=True,
            env=env,
            capture_output=True,
            text=True
        )
        
        # Get backup size
        size_mb = Path(backup_file).stat().st_size / (1024 * 1024)
        print(f"✓ Backup created: {backup_file} ({size_mb:.2f} MB)")
        
        # Verify backup integrity
        if not verify_backup(backup_file, password):
            print()
            print("ERROR: Backup verification failed!")
            print("The backup file may be corrupted.")
            print("Keeping the backup for inspection but marking as failed.")
            sys.exit(1)
        
        print()
        print("=" * 60)
        print("✓ Backup completed successfully")
        print("=" * 60)
        
    except subprocess.CalledProcessError as e:
        print(f"✗ Backup failed:")
        print(f"  Return code: {e.returncode}")
        if e.stderr:
            print(f"  Error output:")
            print(e.stderr)
        sys.exit(1)


def cleanup_old_backups():
    """Remove backups older than RETENTION_DAYS"""
    backup_dir = Path("/backups")
    
    if not backup_dir.exists():
        print("Backup directory doesn't exist, skipping cleanup")
        return
    
    cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
    removed_count = 0
    
    print()
    print(f"Cleaning up backups older than {RETENTION_DAYS} days...")
    
    for backup_file in backup_dir.glob("postgres_*.dump"):
        try:
            # Extract timestamp from filename
            timestamp_str = backup_file.stem.replace("postgres_", "")
            file_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            
            if file_date < cutoff_date:
                backup_file.unlink()
                removed_count += 1
                print(f"  Removed: {backup_file.name}")
        except Exception as e:
            print(f"  Warning: Could not process {backup_file.name}: {e}")
    
    if removed_count > 0:
        print(f"✓ Removed {removed_count} old backup(s)")
    else:
        print("  No old backups to remove")


if __name__ == "__main__":
    try:
        backup()
        cleanup_old_backups()
    except KeyboardInterrupt:
        print("\nBackup interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)