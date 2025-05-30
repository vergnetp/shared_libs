"""
Snapshot Manager

Manages post-deployment snapshots and recovery operations.
Handles snapshot lifecycle, metadata, and fast recovery procedures.
"""

import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path

from digitalocean_manager import DigitalOceanManager
from infrastructure_state import InfrastructureState


class SnapshotMetadata:
    """
    Represents snapshot metadata for tracking and recovery
    """
    
    def __init__(self, snapshot_id: str, droplet_name: str, timestamp: str,
                 service_deployed: str, git_commit: str, snapshot_type: str = "post_deployment"):
        self.snapshot_id = snapshot_id
        self.droplet_name = droplet_name
        self.timestamp = timestamp
        self.service_deployed = service_deployed
        self.git_commit = git_commit
        self.snapshot_type = snapshot_type
        
    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "droplet_name": self.droplet_name,
            "timestamp": self.timestamp,
            "service_deployed": self.service_deployed,
            "git_commit": self.git_commit,
            "snapshot_type": self.snapshot_type
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SnapshotMetadata':
        return cls(
            snapshot_id=data["snapshot_id"],
            droplet_name=data["droplet_name"],
            timestamp=data["timestamp"],
            service_deployed=data["service_deployed"],
            git_commit=data["git_commit"],
            snapshot_type=data.get("snapshot_type", "post_deployment")
        )


class SnapshotManager:
    """
    Manages droplet snapshots for fast recovery with latest deployed code
    """
    
    def __init__(self, do_manager: DigitalOceanManager, infrastructure_state: InfrastructureState,
                 metadata_file: str = "config/snapshot_metadata.json"):
        self.do_manager = do_manager
        self.state = infrastructure_state
        self.metadata_file = Path(metadata_file)
        self.metadata = self._load_metadata()
        
    def _load_metadata(self) -> Dict[str, SnapshotMetadata]:
        """Load snapshot metadata from file"""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, 'r') as f:
                    data = json.load(f)
                    
                return {
                    snapshot_id: SnapshotMetadata.from_dict(meta_data)
                    for snapshot_id, meta_data in data.items()
                }
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Failed to load snapshot metadata: {e}")
        
        return {}
    
    def _save_metadata(self):
        """Save snapshot metadata to file"""
        self.metadata_file.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            snapshot_id: metadata.to_dict()
            for snapshot_id, metadata in self.metadata.items()
        }
        
        with open(self.metadata_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def create_deployment_snapshot(self, droplet_name: str, service_deployed: str, 
                                 git_commit: str) -> Optional[str]:
        """Create snapshot immediately after successful deployment"""
        
        timestamp = datetime.now().strftime('%Y%m%d-%H%M')
        snapshot_name = f"{droplet_name}-deploy-{timestamp}"
        
        print(f"Creating post-deployment snapshot: {snapshot_name}")
        
        try:
            # Create snapshot using DigitalOcean manager
            snapshot_id = self.do_manager.create_snapshot(droplet_name, snapshot_name)
            
            if snapshot_id:
                # Store metadata
                metadata = SnapshotMetadata(
                    snapshot_id=snapshot_id,
                    droplet_name=droplet_name,
                    timestamp=timestamp,
                    service_deployed=service_deployed,
                    git_commit=git_commit,
                    snapshot_type="post_deployment"
                )
                
                self.metadata[snapshot_id] = metadata
                self._save_metadata()
                
                # Cleanup old snapshots for this droplet
                self.cleanup_old_deployment_snapshots(droplet_name, keep=3)
                
                print(f"Post-deployment snapshot created: {snapshot_name} (ID: {snapshot_id})")
                return snapshot_id
            else:
                print(f"Failed to create snapshot for {droplet_name}")
                return None
                
        except Exception as e:
            print(f"Error creating deployment snapshot for {droplet_name}: {e}")
            return None
    
    def get_latest_deployment_snapshot(self, droplet_name: str) -> Optional[SnapshotMetadata]:
        """Get most recent post-deployment snapshot for a droplet"""
        
        droplet_snapshots = [
            metadata for metadata in self.metadata.values()
            if metadata.droplet_name == droplet_name and metadata.snapshot_type == "post_deployment"
        ]
        
        if not droplet_snapshots:
            return None
        
        # Sort by timestamp (newest first)
        droplet_snapshots.sort(key=lambda x: x.timestamp, reverse=True)
        return droplet_snapshots[0]
    
    def create_emergency_snapshot(self, droplet_name: str, reason: str) -> Optional[str]:
        """Create emergency snapshot before recovery operations"""
        
        timestamp = datetime.now().strftime('%Y%m%d-%H%M')
        snapshot_name = f"{droplet_name}-emergency-{timestamp}"
        
        print(f"Creating emergency snapshot: {snapshot_name} (Reason: {reason})")
        
        try:
            snapshot_id = self.do_manager.create_snapshot(droplet_name, snapshot_name)
            
            if snapshot_id:
                metadata = SnapshotMetadata(
                    snapshot_id=snapshot_id,
                    droplet_name=droplet_name,
                    timestamp=timestamp,
                    service_deployed=f"emergency-{reason}",
                    git_commit="unknown",
                    snapshot_type="emergency"
                )
                
                self.metadata[snapshot_id] = metadata
                self._save_metadata()
                
                return snapshot_id
            else:
                return None
                
        except Exception as e:
            print(f"Error creating emergency snapshot for {droplet_name}: {e}")
            return None
    
    def recover_droplet_from_snapshot(self, failed_droplet_name: str, 
                                    new_droplet_name: str = None) -> Dict[str, Any]:
        """Recover failed droplet using latest post-deployment snapshot"""
        
        if not new_droplet_name:
            timestamp = datetime.now().strftime('%Y%m%d-%H%M')
            new_droplet_name = f"{failed_droplet_name}-recovered-{timestamp}"
        
        # Find latest deployment snapshot
        latest_snapshot = self.get_latest_deployment_snapshot(failed_droplet_name)
        
        if not latest_snapshot:
            return {
                'success': False,
                'error': f'No deployment snapshot found for {failed_droplet_name}',
                'fallback': 'full_deployment_recovery'
            }
        
        print(f"Recovering {failed_droplet_name} from snapshot {latest_snapshot.snapshot_id}")
        
        try:
            # Get original droplet configuration
            failed_droplet_config = self.state.get_droplet(failed_droplet_name)
            if not failed_droplet_config:
                return {
                    'success': False,
                    'error': f'Droplet configuration not found for {failed_droplet_name}'
                }
            
            # Create new droplet from snapshot
            new_droplet = self.do_manager.create_from_snapshot(
                snapshot_name=latest_snapshot.snapshot_id,
                new_droplet_name=new_droplet_name,
                size=failed_droplet_config['size'],
                region=failed_droplet_config['region']
            )
            
            if new_droplet:
                # Update infrastructure state
                self.state.add_droplet(
                    name=new_droplet_name,
                    ip=new_droplet.ip_address,
                    size=failed_droplet_config['size'],
                    region=failed_droplet_config['region'],
                    role=failed_droplet_config['role'],
                    monitors=failed_droplet_config.get('monitors', [])
                )
                
                # Update service assignments to new droplet
                self._update_service_assignments(failed_droplet_name, new_droplet_name)
                
                # Remove failed droplet from state
                self.state.remove_droplet(failed_droplet_name)
                
                recovery_info = {
                    'success': True,
                    'new_droplet_name': new_droplet_name,
                    'new_droplet_ip': new_droplet.ip_address,
                    'snapshot_used': latest_snapshot.snapshot_id,
                    'git_commit': latest_snapshot.git_commit,
                    'service_deployed': latest_snapshot.service_deployed,
                    'recovery_time_minutes': 4,  # Typical snapshot recovery time
                    'code_freshness': 'latest_deployed'
                }
                
                print(f"Droplet recovery completed: {failed_droplet_name} → {new_droplet_name}")
                return recovery_info
            else:
                return {
                    'success': False,
                    'error': 'Failed to create droplet from snapshot'
                }
                
        except Exception as e:
            return {
                'success': False,
                'error': f'Recovery failed: {str(e)}'
            }
    
    def _update_service_assignments(self, old_droplet_name: str, new_droplet_name: str):
        """Update service assignments from old droplet to new droplet"""
        
        for project, services in self.state.get_all_projects().items():
            for service_type, service_config in services.items():
                assigned_droplets = service_config.get('assigned_droplets', [])
                
                if old_droplet_name in assigned_droplets:
                    # Replace old droplet with new droplet
                    updated_droplets = [
                        new_droplet_name if d == old_droplet_name else d
                        for d in assigned_droplets
                    ]
                    
                    # Update the service configuration
                    self.state.add_project_service(
                        project=project,
                        service_type=service_type,
                        port=service_config.get('port'),
                        assigned_droplets=updated_droplets,
                        service_config={k: v for k, v in service_config.items() 
                                      if k not in ['port', 'assigned_droplets']}
                    )
    
    def cleanup_old_deployment_snapshots(self, droplet_name: str, keep: int = 3):
        """Remove old deployment snapshots, keeping only the most recent ones"""
        
        droplet_snapshots = [
            (snapshot_id, metadata) for snapshot_id, metadata in self.metadata.items()
            if metadata.droplet_name == droplet_name and metadata.snapshot_type == "post_deployment"
        ]
        
        if len(droplet_snapshots) <= keep:
            return  # Nothing to cleanup
        
        # Sort by timestamp (oldest first)
        droplet_snapshots.sort(key=lambda x: x[1].timestamp)
        
        # Remove old snapshots
        snapshots_to_remove = droplet_snapshots[:-keep]
        
        for snapshot_id, metadata in snapshots_to_remove:
            try:
                # Remove from DigitalOcean
                snapshots = self.do_manager.manager.get_all_snapshots()
                for snapshot in snapshots:
                    if snapshot.id == snapshot_id:
                        snapshot.destroy()
                        break
                
                # Remove from metadata
                del self.metadata[snapshot_id]
                print(f"Cleaned up old snapshot: {metadata.timestamp}")
                
            except Exception as e:
                print(f"Warning: Failed to cleanup snapshot {snapshot_id}: {e}")
        
        # Save updated metadata
        self._save_metadata()
    
    def cleanup_emergency_snapshots(self, older_than_days: int = 7):
        """Remove emergency snapshots older than specified days"""
        
        cutoff_date = datetime.now() - timedelta(days=older_than_days)
        
        emergency_snapshots = [
            (snapshot_id, metadata) for snapshot_id, metadata in self.metadata.items()
            if metadata.snapshot_type == "emergency"
        ]
        
        for snapshot_id, metadata in emergency_snapshots:
            try:
                # Parse timestamp
                snapshot_date = datetime.strptime(metadata.timestamp, '%Y%m%d-%H%M')
                
                if snapshot_date < cutoff_date:
                    # Remove from DigitalOcean
                    snapshots = self.do_manager.manager.get_all_snapshots()
                    for snapshot in snapshots:
                        if snapshot.id == snapshot_id:
                            snapshot.destroy()
                            break
                    
                    # Remove from metadata
                    del self.metadata[snapshot_id]
                    print(f"Cleaned up old emergency snapshot: {metadata.timestamp}")
                    
            except Exception as e:
                print(f"Warning: Failed to cleanup emergency snapshot {snapshot_id}: {e}")
        
        # Save updated metadata
        self._save_metadata()
    
    def get_snapshot_summary(self) -> Dict[str, Any]:
        """Get summary of all snapshots"""
        
        deployment_snapshots = []
        emergency_snapshots = []
        
        for metadata in self.metadata.values():
            if metadata.snapshot_type == "post_deployment":
                deployment_snapshots.append(metadata.to_dict())
            elif metadata.snapshot_type == "emergency":
                emergency_snapshots.append(metadata.to_dict())
        
        # Group by droplet
        droplet_summary = {}
        for metadata in self.metadata.values():
            droplet_name = metadata.droplet_name
            if droplet_name not in droplet_summary:
                droplet_summary[droplet_name] = {
                    'deployment_snapshots': 0,
                    'emergency_snapshots': 0,
                    'latest_deployment': None
                }
            
            if metadata.snapshot_type == "post_deployment":
                droplet_summary[droplet_name]['deployment_snapshots'] += 1
                
                # Track latest deployment snapshot
                if (not droplet_summary[droplet_name]['latest_deployment'] or
                    metadata.timestamp > droplet_summary[droplet_name]['latest_deployment']['timestamp']):
                    droplet_summary[droplet_name]['latest_deployment'] = metadata.to_dict()
                    
            elif metadata.snapshot_type == "emergency":
                droplet_summary[droplet_name]['emergency_snapshots'] += 1
        
        return {
            'total_snapshots': len(self.metadata),
            'deployment_snapshots': len(deployment_snapshots),
            'emergency_snapshots': len(emergency_snapshots),
            'droplet_summary': droplet_summary,
            'oldest_snapshot': min(self.metadata.values(), key=lambda x: x.timestamp).timestamp if self.metadata else None,
            'newest_snapshot': max(self.metadata.values(), key=lambda x: x.timestamp).timestamp if self.metadata else None
        }
    
    def validate_snapshots(self) -> Dict[str, Any]:
        """Validate that snapshots still exist in DigitalOcean"""
        
        try:
            # Get all snapshots from DigitalOcean
            do_snapshots = self.do_manager.manager.get_all_snapshots()
            do_snapshot_ids = {s.id for s in do_snapshots}
            
            # Check which metadata snapshots still exist
            valid_snapshots = []
            invalid_snapshots = []
            
            for snapshot_id, metadata in self.metadata.items():
                if snapshot_id in do_snapshot_ids:
                    valid_snapshots.append(snapshot_id)
                else:
                    invalid_snapshots.append(snapshot_id)
            
            # Remove invalid snapshots from metadata
            for snapshot_id in invalid_snapshots:
                del self.metadata[snapshot_id]
            
            if invalid_snapshots:
                self._save_metadata()
                print(f"Removed {len(invalid_snapshots)} invalid snapshot references")
            
            return {
                'valid_snapshots': len(valid_snapshots),
                'invalid_snapshots': len(invalid_snapshots),
                'removed_invalid': invalid_snapshots
            }
            
        except Exception as e:
            return {
                'error': f'Failed to validate snapshots: {str(e)}'
            }
    
    def get_recovery_options(self, droplet_name: str) -> Dict[str, Any]:
        """Get available recovery options for a droplet"""
        
        latest_deployment = self.get_latest_deployment_snapshot(droplet_name)
        
        emergency_snapshots = [
            metadata for metadata in self.metadata.values()
            if metadata.droplet_name == droplet_name and metadata.snapshot_type == "emergency"
        ]
        
        # Sort emergency snapshots by timestamp (newest first)
        emergency_snapshots.sort(key=lambda x: x.timestamp, reverse=True)
        
        return {
            'droplet_name': droplet_name,
            'latest_deployment_snapshot': latest_deployment.to_dict() if latest_deployment else None,
            'emergency_snapshots': [s.to_dict() for s in emergency_snapshots[:5]],  # Latest 5
            'recovery_time_estimate': {
                'snapshot_recovery': '3-5 minutes',
                'full_deployment': '8-15 minutes'
            },
            'recommended_action': 'snapshot_recovery' if latest_deployment else 'full_deployment'
        }
