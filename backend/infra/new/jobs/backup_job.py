"""
Backup job that uses your existing PostgreSQLBackupManager
Usage: python backup_job.py <project> <env> <service> [backup_name]
"""
import sys
import os
from pathlib import Path

# Add parent directory to path to import your modules
sys.path.insert(0, '/app')

from backup_manager import PostgreSQLBackupManager
from enums import Envs


def main():
    if len(sys.argv) < 4:
        print("Usage: python backup_job.py <project> <env> <service> [backup_name]")
        sys.exit(1)
    
    project_name = sys.argv[1]
    env = Envs(sys.argv[2])
    service_name = sys.argv[3]
    backup_name = sys.argv[4] if len(sys.argv) > 4 else None
    
    print(f"🔄 Starting backup job for {project_name}/{env.value}/{service_name}")
    
    try:
        # Create backup manager using your existing code
        mgr = PostgreSQLBackupManager(project_name, env, service_name)
        
        # Create backup
        result = mgr.create_backup(backup_name)
        
        if result:
            print(f"✅ Backup created successfully: {result}")
            
            # Cleanup old backups
            cleanup_count = mgr.cleanup_old_backups(7)
            print(f"🧹 Cleaned up {cleanup_count} old backups")
            
            # List current backups
            backups = mgr.list_backups()
            print(f"📁 Total backups: {len(backups)}")
            
        else:
            print("❌ Backup failed")
            sys.exit(1)
            
    except Exception as e:
        print(f"❌ Backup job error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()