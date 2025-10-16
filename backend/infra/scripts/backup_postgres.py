#!/usr/bin/env python3
"""
Postgres Backup Script with Verification

Runs as a scheduled container to backup Postgres database.
Connects to parent Postgres container via Docker DNS.
Verifies backup integrity after creation.
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
POSTGRES_PASSWORD_FILE = os.environ.get("POSTGRES_PASSWORD_FILE", "/run/secrets/db_password")

# Backup-specific env vars
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 7))
SERVICE_NAME = os.environ.get("SERVICE_NAME", "postgres")
PROJECT = os.environ.get("PROJECT", "unknown")
ENV = os.environ.get("ENV", "unknown")


def get_db_password():
    """Read password from mounted secret file"""
    password_file = Path(POSTGRES_PASSWORD_FILE)
    if not password_file.exists():
        raise FileNotFoundError(f"Password file not found: {POSTGRES_PASSWORD_FILE}")
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
    print(f"  Timestamp: {timestamp}")
    print(f"  Host: {HOST}")
    print(f"  Database: {POSTGRES_DB}")
    print(f"  User: {POSTGRES_USER}")
    print(f"  Output: {backup_file}")
    print()
    
    # Get password
    try:
        password = get_db_password()
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
        print("Cleaning up old backups...")
        cleanup_old_backups()
        
        print()
        print("=" * 60)
        print("Backup completed successfully")
        print("=" * 60)
        sys.exit(0)
        
    except subprocess.CalledProcessError as e:
        print()
        print("=" * 60)
        print("ERROR: Backup failed")
        print("=" * 60)
        print(f"  Command: {' '.join(cmd)}")
        print(f"  Return code: {e.returncode}")
        if e.stderr:
            print(f"  stderr:")
            print(e.stderr)
        sys.exit(1)


def cleanup_old_backups():
    """Remove backups older than retention period"""
    cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
    backup_dir = Path("/backups")
    
    if not backup_dir.exists():
        print("Warning: Backup directory does not exist")
        return
    
    removed_count = 0
    kept_count = 0
    
    for backup_file in backup_dir.glob("postgres_*.dump"):
        try:
            # Extract timestamp from filename: postgres_20240315_143022.dump
            timestamp_str = backup_file.stem.replace("postgres_", "")
            file_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            
            if file_date < cutoff_date:
                size_mb = backup_file.stat().st_size / (1024 * 1024)
                backup_file.unlink()
                removed_count += 1
                print(f"  Removed old backup: {backup_file.name} ({size_mb:.2f} MB)")
            else:
                kept_count += 1
                
        except Exception as e:
            print(f"  Warning: Could not process {backup_file.name}: {e}")
    
    print(f"  Cleanup complete: {removed_count} removed, {kept_count} kept")
    print(f"  Retention policy: {RETENTION_DAYS} days")


if __name__ == "__main__":
    backup()