"""
Deployment History - Track deployments for rollback and audit.

Stores deployment records with metadata for:
- Rollback to previous versions
- Audit trail (who, when, why)
- Deployment analytics
"""

from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional
from pathlib import Path
from enum import Enum


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class DeploymentRecord:
    """A single deployment record."""
    id: str
    workspace_id: str
    project: str
    environment: str
    service_name: str
    
    # What was deployed
    source_type: str  # code, git, image
    image_name: Optional[str] = None  # Docker image name/tag
    image_digest: Optional[str] = None  # Image SHA for exact version
    git_url: Optional[str] = None
    git_branch: Optional[str] = None
    git_commit: Optional[str] = None
    
    # Where it was deployed
    server_ips: List[str] = field(default_factory=list)
    container_name: Optional[str] = None
    
    # Configuration snapshot
    port: int = 8000
    env_vars: Dict[str, str] = field(default_factory=dict)
    
    # Metadata
    deployed_at: Optional[datetime] = None
    deployed_by: Optional[str] = None  # User ID
    deployed_by_name: Optional[str] = None  # User display name
    comment: Optional[str] = None  # Deployment notes
    
    # Status
    status: DeploymentStatus = DeploymentStatus.PENDING
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None
    
    # Rollback info
    is_rollback: bool = False
    rollback_from_id: Optional[str] = None  # If this is a rollback, what deployment it rolled back from
    rolled_back_at: Optional[datetime] = None
    rolled_back_by: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        d['deployed_at'] = self.deployed_at.isoformat() if self.deployed_at else None
        d['rolled_back_at'] = self.rolled_back_at.isoformat() if self.rolled_back_at else None
        d['status'] = self.status.value
        return d
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DeploymentRecord':
        """Create from dictionary."""
        data = data.copy()
        if data.get('deployed_at'):
            data['deployed_at'] = datetime.fromisoformat(data['deployed_at'])
        if data.get('rolled_back_at'):
            data['rolled_back_at'] = datetime.fromisoformat(data['rolled_back_at'])
        if data.get('status'):
            data['status'] = DeploymentStatus(data['status'])
        if isinstance(data.get('server_ips'), str):
            data['server_ips'] = json.loads(data['server_ips'])
        if isinstance(data.get('env_vars'), str):
            data['env_vars'] = json.loads(data['env_vars'])
        return cls(**data)


class DeploymentHistory:
    """
    Deployment history storage using SQLite.
    
    Usage:
        history = DeploymentHistory("/path/to/data")
        
        # Record a deployment
        record = history.record_deployment(
            workspace_id="ws123",
            project="myapp",
            environment="prod",
            service_name="api",
            source_type="git",
            git_url="https://github.com/...",
            deployed_by="user123",
            comment="Fix login bug",
        )
        
        # Get deployment history
        deployments = history.get_history("ws123", "myapp", "prod", "api", limit=10)
        
        # Get previous successful deployment for rollback
        previous = history.get_previous_deployment("ws123", "myapp", "prod", "api")
    """
    
    # How many deployments to keep per service
    MAX_HISTORY_PER_SERVICE = 20
    
    def __init__(self, data_dir: str = "/app/data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "deployments.db"
        self._init_db()
    
    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS deployments (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    service_name TEXT NOT NULL,
                    
                    source_type TEXT NOT NULL,
                    image_name TEXT,
                    image_digest TEXT,
                    git_url TEXT,
                    git_branch TEXT,
                    git_commit TEXT,
                    
                    server_ips TEXT,
                    container_name TEXT,
                    
                    port INTEGER DEFAULT 8000,
                    env_vars TEXT,
                    
                    deployed_at TEXT,
                    deployed_by TEXT,
                    deployed_by_name TEXT,
                    comment TEXT,
                    
                    status TEXT DEFAULT 'pending',
                    error_message TEXT,
                    duration_seconds REAL,
                    
                    is_rollback INTEGER DEFAULT 0,
                    rollback_from_id TEXT,
                    rolled_back_at TEXT,
                    rolled_back_by TEXT,
                    
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Index for fast lookups
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_deployments_service 
                ON deployments(workspace_id, project, environment, service_name)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_deployments_deployed_at 
                ON deployments(deployed_at DESC)
            """)
            conn.commit()
    
    def _generate_id(self) -> str:
        """Generate a unique deployment ID."""
        import uuid
        return f"deploy_{uuid.uuid4().hex[:12]}"
    
    def record_deployment(
        self,
        workspace_id: str,
        project: str,
        environment: str,
        service_name: str,
        source_type: str,
        deployed_by: Optional[str] = None,
        deployed_by_name: Optional[str] = None,
        comment: Optional[str] = None,
        **kwargs,
    ) -> DeploymentRecord:
        """
        Record a new deployment.
        
        Returns the created DeploymentRecord with a unique ID.
        """
        record = DeploymentRecord(
            id=self._generate_id(),
            workspace_id=workspace_id,
            project=project,
            environment=environment,
            service_name=service_name,
            source_type=source_type,
            deployed_at=datetime.utcnow(),
            deployed_by=deployed_by,
            deployed_by_name=deployed_by_name,
            comment=comment,
            status=DeploymentStatus.IN_PROGRESS,
            **kwargs,
        )
        
        self._save_record(record)
        return record
    
    def _save_record(self, record: DeploymentRecord):
        """Save a deployment record to the database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO deployments (
                    id, workspace_id, project, environment, service_name,
                    source_type, image_name, image_digest, git_url, git_branch, git_commit,
                    server_ips, container_name, port, env_vars,
                    deployed_at, deployed_by, deployed_by_name, comment,
                    status, error_message, duration_seconds,
                    is_rollback, rollback_from_id, rolled_back_at, rolled_back_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.id, record.workspace_id, record.project, record.environment, record.service_name,
                record.source_type, record.image_name, record.image_digest,
                record.git_url, record.git_branch, record.git_commit,
                json.dumps(record.server_ips), record.container_name, record.port,
                json.dumps(record.env_vars),
                record.deployed_at.isoformat() if record.deployed_at else None,
                record.deployed_by, record.deployed_by_name, record.comment,
                record.status.value, record.error_message, record.duration_seconds,
                1 if record.is_rollback else 0, record.rollback_from_id,
                record.rolled_back_at.isoformat() if record.rolled_back_at else None,
                record.rolled_back_by,
            ))
            conn.commit()
    
    def update_status(
        self,
        deployment_id: str,
        status: DeploymentStatus,
        error_message: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        image_name: Optional[str] = None,
        image_digest: Optional[str] = None,
        container_name: Optional[str] = None,
        server_ips: Optional[List[str]] = None,
    ):
        """Update the status of a deployment."""
        updates = ["status = ?"]
        values = [status.value]
        
        if error_message is not None:
            updates.append("error_message = ?")
            values.append(error_message)
        if duration_seconds is not None:
            updates.append("duration_seconds = ?")
            values.append(duration_seconds)
        if image_name is not None:
            updates.append("image_name = ?")
            values.append(image_name)
        if image_digest is not None:
            updates.append("image_digest = ?")
            values.append(image_digest)
        if container_name is not None:
            updates.append("container_name = ?")
            values.append(container_name)
        if server_ips is not None:
            updates.append("server_ips = ?")
            values.append(json.dumps(server_ips))
        
        values.append(deployment_id)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"""
                UPDATE deployments SET {', '.join(updates)} WHERE id = ?
            """, values)
            conn.commit()
    
    def get_deployment(self, deployment_id: str) -> Optional[DeploymentRecord]:
        """Get a deployment by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM deployments WHERE id = ?",
                (deployment_id,)
            )
            row = cursor.fetchone()
            if row:
                return DeploymentRecord.from_dict(dict(row))
        return None
    
    def get_history(
        self,
        workspace_id: str,
        project: str,
        environment: str,
        service_name: str,
        limit: int = 10,
        include_failed: bool = True,
    ) -> List[DeploymentRecord]:
        """Get deployment history for a service."""
        query = """
            SELECT * FROM deployments 
            WHERE workspace_id = ? AND project = ? AND environment = ? AND service_name = ?
        """
        params = [workspace_id, project, environment, service_name]
        
        if not include_failed:
            query += " AND status = 'success'"
        
        query += " ORDER BY deployed_at DESC LIMIT ?"
        params.append(limit)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [DeploymentRecord.from_dict(dict(row)) for row in cursor.fetchall()]
    
    def get_previous_deployment(
        self,
        workspace_id: str,
        project: str,
        environment: str,
        service_name: str,
        exclude_id: Optional[str] = None,
    ) -> Optional[DeploymentRecord]:
        """
        Get the previous successful deployment for rollback.
        
        Args:
            exclude_id: Exclude this deployment ID (usually the current one)
        """
        query = """
            SELECT * FROM deployments 
            WHERE workspace_id = ? AND project = ? AND environment = ? AND service_name = ?
            AND status = 'success'
        """
        params = [workspace_id, project, environment, service_name]
        
        if exclude_id:
            query += " AND id != ?"
            params.append(exclude_id)
        
        query += " ORDER BY deployed_at DESC LIMIT 1"
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            if row:
                return DeploymentRecord.from_dict(dict(row))
        return None
    
    def get_all_history(
        self,
        workspace_id: str,
        limit: int = 50,
        project: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> List[DeploymentRecord]:
        """Get deployment history across all services."""
        query = "SELECT * FROM deployments WHERE workspace_id = ?"
        params = [workspace_id]
        
        if project:
            query += " AND project = ?"
            params.append(project)
        if environment:
            query += " AND environment = ?"
            params.append(environment)
        
        query += " ORDER BY deployed_at DESC LIMIT ?"
        params.append(limit)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [DeploymentRecord.from_dict(dict(row)) for row in cursor.fetchall()]
    
    def mark_rolled_back(
        self,
        deployment_id: str,
        rolled_back_by: Optional[str] = None,
    ):
        """Mark a deployment as rolled back."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE deployments 
                SET status = ?, rolled_back_at = ?, rolled_back_by = ?
                WHERE id = ?
            """, (
                DeploymentStatus.ROLLED_BACK.value,
                datetime.utcnow().isoformat(),
                rolled_back_by,
                deployment_id,
            ))
            conn.commit()
    
    def cleanup_old_deployments(
        self,
        workspace_id: str,
        project: str,
        environment: str,
        service_name: str,
        keep: int = None,
    ):
        """
        Remove old deployment records, keeping only the most recent N.
        
        Note: This only removes records, not actual Docker images.
        """
        keep = keep or self.MAX_HISTORY_PER_SERVICE
        
        with sqlite3.connect(self.db_path) as conn:
            # Get IDs to keep
            cursor = conn.execute("""
                SELECT id FROM deployments 
                WHERE workspace_id = ? AND project = ? AND environment = ? AND service_name = ?
                ORDER BY deployed_at DESC LIMIT ?
            """, (workspace_id, project, environment, service_name, keep))
            keep_ids = [row[0] for row in cursor.fetchall()]
            
            if keep_ids:
                placeholders = ','.join('?' * len(keep_ids))
                conn.execute(f"""
                    DELETE FROM deployments 
                    WHERE workspace_id = ? AND project = ? AND environment = ? AND service_name = ?
                    AND id NOT IN ({placeholders})
                """, (workspace_id, project, environment, service_name, *keep_ids))
                conn.commit()


# Convenience function
_history: Optional[DeploymentHistory] = None

def get_deployment_history(data_dir: str = "/app/data") -> DeploymentHistory:
    """Get or create the global deployment history instance."""
    global _history
    if _history is None:
        _history = DeploymentHistory(data_dir)
    return _history
