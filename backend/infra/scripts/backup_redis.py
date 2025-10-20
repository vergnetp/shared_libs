#!/usr/bin/env python3
"""
Redis Backup Script with Verification

Runs as a scheduled container to backup Redis database.
Connects to parent Redis container via Docker DNS.
Creates RDB snapshot and verifies integrity.

IMPORTANT: This script does NOT import ResourceResolver or any infra libraries.
It relies purely on environment variables injected by BackupManager.
"""

import os
import subprocess
import sys
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep

# Environment variables (inherited from parent redis config)
HOST = os.environ.get("HOST", "localhost")  # Container name
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")

# CRITICAL: Generic PASSWORD_FILE injected by BackupManager
# This is the same for all services - no need for REDIS_PASSWORD_FILE
PASSWORD_FILE = os.environ.get("PASSWORD_FILE", "/run/secrets/redis_password")

# Fallback for backward compatibility with old deployments
REDIS_PASSWORD_FILE = os.environ.get("REDIS_PASSWORD_FILE", PASSWORD_FILE)

# Backup-specific env vars
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 7))
SERVICE_NAME = os.environ.get("SERVICE_NAME", "redis")
PROJECT = os.environ.get("PROJECT", "unknown")
ENV = os.environ.get("ENV", "unknown")


def get_password():
    """Read password from mounted secret file"""
    password_file = Path(REDIS_PASSWORD_FILE)
    if not password_file.exists():
        raise FileNotFoundError(
            f"Password file not found: {REDIS_PASSWORD_FILE}\n"
            f"Ensure the secrets volume is mounted and PASSWORD_FILE env var is set correctly."
        )
    return password_file.read_text().strip()


def wait_for_redis(max_retries: int = 30, delay: int = 2):
    """Wait for Redis to be ready"""
    print(f"Waiting for Redis at {HOST}:{REDIS_PORT}...")
    
    password = get_password()
    
    for i in range(max_retries):
        try:
            # Try to ping Redis
            cmd = [
                "redis-cli",
                "-h", HOST,
                "-p", REDIS_PORT,
                "-a", password,
                "PING"
            ]
            
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if "PONG" in result.stdout:
                print(f"✓ Redis is ready")
                return True
                
        except Exception as e:
            if i < max_retries - 1:
                print(f"  Attempt {i+1}/{max_retries}: Not ready yet, waiting {delay}s...")
                sleep(delay)
            else:
                print(f"✗ Redis not ready after {max_retries} attempts")
                return False
    
    return False


def trigger_bgsave():
    """Trigger Redis BGSAVE command to create RDB snapshot"""
    print(f"\nTriggering Redis BGSAVE...")
    
    password = get_password()
    
    cmd = [
        "redis-cli",
        "-h", HOST,
        "-p", REDIS_PORT,
        "-a", password,
        "BGSAVE"
    ]
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if "Background saving started" in result.stdout:
            print(f"✓ BGSAVE triggered successfully")
            return True
        else:
            print(f"Warning: Unexpected response: {result.stdout}")
            return True  # Still proceed
            
    except subprocess.CalledProcessError as e:
        print(f"✗ BGSAVE failed:")
        print(f"  Return code: {e.returncode}")
        if e.stderr:
            print(f"  Error: {e.stderr}")
        return False


def wait_for_bgsave_complete(max_wait: int = 300, interval: int = 2):
    """Wait for BGSAVE to complete"""
    print(f"\nWaiting for BGSAVE to complete...")
    
    password = get_password()
    elapsed = 0
    
    while elapsed < max_wait:
        sleep(interval)
        elapsed += interval
        
        # Check last save time
        cmd = [
            "redis-cli",
            "-h", HOST,
            "-p", REDIS_PORT,
            "-a", password,
            "LASTSAVE"
        ]
        
        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # Also check if BGSAVE is still in progress
            info_cmd = [
                "redis-cli",
                "-h", HOST,
                "-p", REDIS_PORT,
                "-a", password,
                "INFO", "persistence"
            ]
            
            info_result = subprocess.run(
                info_cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # Parse INFO output
            info_lines = info_result.stdout.split('\n')
            rdb_bgsave_in_progress = False
            
            for line in info_lines:
                if line.startswith("rdb_bgsave_in_progress:"):
                    value = line.split(':')[1].strip()
                    rdb_bgsave_in_progress = (value == "1")
                    break
            
            if not rdb_bgsave_in_progress:
                print(f"✓ BGSAVE completed ({elapsed}s elapsed)")
                return True
            else:
                print(f"  BGSAVE still in progress... ({elapsed}s elapsed)")
                
        except Exception as e:
            print(f"  Warning: Could not check BGSAVE status: {e}")
    
    print(f"✗ BGSAVE timed out after {max_wait}s")
    return False


def copy_rdb_to_backup(timestamp: str):
    """Copy RDB file from data volume to backups volume"""
    print(f"\nCopying RDB file to backups...")
    
    # Redis stores RDB in /data/dump.rdb
    source_rdb = Path("/data/dump.rdb")
    
    if not source_rdb.exists():
        print(f"✗ RDB file not found at {source_rdb}")
        return None
    
    # Create timestamped backup
    backup_filename = f"redis_{timestamp}.rdb"
    backup_path = Path("/backups") / backup_filename
    
    try:
        shutil.copy2(source_rdb, backup_path)
        
        # Get file size
        size_mb = backup_path.stat().st_size / (1024 * 1024)
        print(f"✓ RDB copied to: {backup_path} ({size_mb:.2f} MB)")
        
        return backup_path
        
    except Exception as e:
        print(f"✗ Failed to copy RDB: {e}")
        return None


def verify_rdb(backup_path: Path):
    """Verify RDB file integrity using redis-check-rdb"""
    print(f"\nVerifying RDB integrity...")
    
    cmd = ["redis-check-rdb", str(backup_path)]
    
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=60
        )
        
        # redis-check-rdb outputs "RDB OK" if valid
        if "is valid" in result.stdout.lower() or result.returncode == 0:
            print(f"✓ RDB file is valid")
            return True
        else:
            print(f"✗ RDB validation failed")
            print(f"  Output: {result.stdout}")
            return False
            
    except subprocess.CalledProcessError as e:
        print(f"✗ RDB validation failed:")
        print(f"  Return code: {e.returncode}")
        if e.stdout:
            print(f"  Output: {e.stdout}")
        if e.stderr:
            print(f"  Error: {e.stderr}")
        return False
    except Exception as e:
        print(f"✗ RDB validation error: {e}")
        return False


def backup():
    """Create and verify a Redis RDB backup"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    print("=" * 60)
    print(f"Redis Backup Starting")
    print("=" * 60)
    print(f"  Project: {PROJECT}")
    print(f"  Environment: {ENV}")
    print(f"  Service: {SERVICE_NAME}")
    print(f"  Timestamp: {timestamp}")
    print(f"  Host: {HOST}:{REDIS_PORT}")
    print(f"  Password File: {REDIS_PASSWORD_FILE}")
    print()
    
    # Wait for Redis to be ready
    if not wait_for_redis():
        print("ERROR: Redis is not ready")
        sys.exit(1)
    
    # Trigger BGSAVE
    if not trigger_bgsave():
        print("ERROR: Could not trigger BGSAVE")
        sys.exit(1)
    
    # Wait for BGSAVE to complete
    if not wait_for_bgsave_complete():
        print("ERROR: BGSAVE did not complete")
        sys.exit(1)
    
    # Copy RDB to backup location
    backup_path = copy_rdb_to_backup(timestamp)
    if not backup_path:
        print("ERROR: Could not copy RDB file")
        sys.exit(1)
    
    # Verify backup
    if not verify_rdb(backup_path):
        print("ERROR: Backup verification failed")
        print("The backup file may be corrupted.")
        print("Keeping the backup for inspection but marking as failed.")
        sys.exit(1)
    
    print()
    print("=" * 60)
    print("✓ Backup completed successfully")
    print("=" * 60)


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
    
    for backup_file in backup_dir.glob("redis_*.rdb"):
        try:
            # Extract timestamp from filename
            timestamp_str = backup_file.stem.replace("redis_", "")
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