#!/usr/bin/env python3
"""
Postgres Backup Script

Runs as a scheduled container to backup Postgres database.
Connects to parent Postgres container via Docker DNS.
"""

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

# Environment variables (inherited from parent postgres config)
HOST = os.environ.get("HOST", "localhost")  # Container name
POSTGRES_DB = os.environ.get("POSTGRES_DB")                   # e.g., "new_project_8e9fb088"
POSTGRES_USER = os.environ.get("POSTGRES_USER")               # e.g., "new_project_user"
POSTGRES_PASSWORD_FILE = os.environ.get("POSTGRES_PASSWORD_FILE", "/run/secrets/db_password")

# Backup-specific env vars
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 7))
SERVICE_NAME = os.environ.get("SERVICE_NAME", "postgres")


def get_db_password():
    """Read password from mounted secret file"""
    password_file = Path(POSTGRES_PASSWORD_FILE)
    if not password_file.exists():
        raise FileNotFoundError(f"Password file not found: {POSTGRES_PASSWORD_FILE}")
    return password_file.read_text().strip()


def backup():
    """Create a compressed backup of the Postgres database"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"/backups/postgres_{timestamp}.dump"
    
    print(f"Starting backup: {backup_file}")
    print(f"  Host: {HOST}")
    print(f"  Database: {POSTGRES_DB}")
    print(f"  User: {POSTGRES_USER}")
    
    # Get password
    try:
        password = get_db_password()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        exit(1)
    
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
        "-f", backup_file
    ]
    
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
        
        # Cleanup old backups
        cleanup_old_backups()
        
        exit(0)
        
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Backup failed")
        print(f"  Command: {' '.join(cmd)}")
        print(f"  Return code: {e.returncode}")
        if e.stderr:
            print(f"  stderr: {e.stderr}")
        exit(1)


def cleanup_old_backups():
    """Remove backups older than retention period"""
    cutoff_date = datetime.now() - timedelta(days=RETENTION_DAYS)
    backup_dir = Path("/backups")
    
    if not backup_dir.exists():
        return
    
    deleted = 0
    total_size_freed = 0
    
    for backup_file in backup_dir.glob("postgres_*.dump"):
        try:
            # Extract timestamp from filename
            timestamp_str = backup_file.stem.replace("postgres_", "")
            file_date = datetime.strptime(timestamp_str, "%Y%m%d_%H%M%S")
            
            if file_date < cutoff_date:
                size = backup_file.stat().st_size
                backup_file.unlink()
                deleted += 1
                total_size_freed += size
                print(f"  Deleted old backup: {backup_file.name}")
                
        except (ValueError, OSError) as e:
            print(f"  Warning: Could not process {backup_file.name}: {e}")
    
    if deleted > 0:
        size_mb = total_size_freed / (1024 * 1024)
        print(f"✓ Cleaned up {deleted} old backup(s), freed {size_mb:.2f} MB")


if __name__ == "__main__":
    # Verify required env vars
    if not HOST or not POSTGRES_DB or not POSTGRES_USER:
        print("ERROR: Missing required environment variables")
        print(f"  POSTGRES_HOST: {HOST}")
        print(f"  POSTGRES_DB: {POSTGRES_DB}")
        print(f"  POSTGRES_USER: {POSTGRES_USER}")
        exit(1)
    
    backup()