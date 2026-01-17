"""
Event Storage - Persist stream events to OpenSearch.

Optional component for storing stream events for debugging, analytics,
and audit trails. Events can be persisted in addition to Redis Pub/Sub.

Usage:
    # Initialize storage
    init_event_storage(
        host="localhost",
        port=9200,
        index_prefix="stream_events",
    )
    
    # In StreamContext, events are auto-persisted if persist_events=True
    ctx = StreamContext.create(persist_events=True, ...)
    ctx.log("This goes to Redis AND OpenSearch")
    
    # Query events
    storage = get_event_storage()
    events = storage.query(
        channel_id="abc123",
        event_types=["log", "error"],
        limit=100,
    )
"""

from __future__ import annotations
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List, TYPE_CHECKING

from .events import StreamEvent

if TYPE_CHECKING:
    from opensearchpy import OpenSearch


@dataclass
class EventStorageConfig:
    """
    Configuration for event storage.
    
    Attributes:
        host: OpenSearch host
        port: OpenSearch port
        use_ssl: Whether to use SSL
        index_prefix: Prefix for index names
        auth_type: Authentication type (none, basic, aws)
        username: Username for basic auth
        password: Password for basic auth
        region: AWS region for aws auth
        verify_certs: Whether to verify SSL certs
        timeout: Connection timeout
        batch_size: Max events per batch write
    """
    host: str = "localhost"
    port: int = 9200
    use_ssl: bool = False
    index_prefix: str = "stream_events"
    auth_type: str = "none"  # none, basic, aws
    username: Optional[str] = None
    password: Optional[str] = None
    region: str = "us-east-1"
    verify_certs: bool = False
    timeout: int = 30
    batch_size: int = 100
    
    @classmethod
    def from_env(cls) -> 'EventStorageConfig':
        """Create config from environment variables."""
        return cls(
            host=os.getenv("OPENSEARCH_HOST", "localhost"),
            port=int(os.getenv("OPENSEARCH_PORT", "9200")),
            use_ssl=os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true",
            index_prefix=os.getenv("OPENSEARCH_INDEX_PREFIX", "stream_events"),
            auth_type=os.getenv("OPENSEARCH_AUTH_TYPE", "none"),
            username=os.getenv("OPENSEARCH_USERNAME"),
            password=os.getenv("OPENSEARCH_PASSWORD"),
            region=os.getenv("AWS_REGION", "us-east-1"),
            verify_certs=os.getenv("OPENSEARCH_VERIFY_CERTS", "false").lower() == "true",
        )


class EventStorageInterface(ABC):
    """Abstract interface for event storage backends."""
    
    @abstractmethod
    def store(self, event: StreamEvent) -> Dict[str, Any]:
        """Store a single event. Returns storage result."""
        pass
    
    @abstractmethod
    def store_batch(self, events: List[StreamEvent]) -> Dict[str, Any]:
        """Store a batch of events. Returns storage result."""
        pass
    
    @abstractmethod
    def query(
        self,
        channel_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[StreamEvent]:
        """Query stored events."""
        pass


class OpenSearchEventStorage(EventStorageInterface):
    """
    OpenSearch implementation for event storage.
    
    Stores events in date-partitioned indices for efficient querying
    and automatic retention management.
    """
    
    def __init__(self, config: Optional[EventStorageConfig] = None):
        """
        Initialize OpenSearch event storage.
        
        Args:
            config: Storage configuration (uses defaults/env if not provided)
        """
        self.cfg = config or EventStorageConfig.from_env()
        self._client: Optional['OpenSearch'] = None
        self._indices_created: set = set()
    
    def _get_client(self) -> 'OpenSearch':
        """Get or create OpenSearch client."""
        if self._client is None:
            from opensearchpy import OpenSearch, RequestsHttpConnection
            
            # Configure authentication
            auth = None
            if self.cfg.auth_type == "aws":
                from requests_aws4auth import AWS4Auth
                import boto3
                
                session = boto3.Session()
                credentials = session.get_credentials()
                auth = AWS4Auth(
                    credentials.access_key,
                    credentials.secret_key,
                    self.cfg.region,
                    "es",
                    session_token=credentials.token,
                )
            elif self.cfg.auth_type == "basic":
                if self.cfg.username and self.cfg.password:
                    auth = (self.cfg.username, self.cfg.password)
            
            self._client = OpenSearch(
                hosts=[{"host": self.cfg.host, "port": self.cfg.port}],
                http_auth=auth,
                use_ssl=self.cfg.use_ssl,
                verify_certs=self.cfg.verify_certs,
                connection_class=RequestsHttpConnection,
                timeout=self.cfg.timeout,
            )
        
        return self._client
    
    def _get_index_name(self, timestamp: Optional[str] = None) -> str:
        """Get index name based on date."""
        if timestamp:
            try:
                # Parse ISO timestamp
                if "T" in timestamp:
                    date_part = timestamp.split("T")[0]
                else:
                    date_part = timestamp.split()[0]
                return f"{self.cfg.index_prefix}-{date_part.replace('-', '.')}"
            except Exception:
                pass
        
        # Default to today
        return f"{self.cfg.index_prefix}-{datetime.utcnow().strftime('%Y.%m.%d')}"
    
    def _ensure_index(self, index_name: str) -> None:
        """Ensure index exists with correct mappings."""
        if index_name in self._indices_created:
            return
        
        client = self._get_client()
        
        if not client.indices.exists(index=index_name):
            mappings = {
                "mappings": {
                    "properties": {
                        "type": {"type": "keyword"},
                        "channel_id": {"type": "keyword"},
                        "event_id": {"type": "keyword"},
                        "timestamp": {"type": "date"},
                        "data": {"type": "object", "dynamic": True},
                        "context": {
                            "type": "object",
                            "properties": {
                                "workspace_id": {"type": "keyword"},
                                "project": {"type": "keyword"},
                                "env": {"type": "keyword"},
                                "service": {"type": "keyword"},
                            },
                            "dynamic": True,
                        },
                    }
                }
            }
            
            client.indices.create(index=index_name, body=mappings)
        
        self._indices_created.add(index_name)
    
    def store(self, event: StreamEvent) -> Dict[str, Any]:
        """Store a single event."""
        index_name = self._get_index_name(event.timestamp)
        self._ensure_index(index_name)
        
        client = self._get_client()
        
        try:
            response = client.index(
                index=index_name,
                body=event.to_dict(),
                refresh=False,
            )
            return {"status": "indexed", "id": response.get("_id")}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def store_batch(self, events: List[StreamEvent]) -> Dict[str, Any]:
        """Store a batch of events."""
        if not events:
            return {"status": "empty", "count": 0}
        
        client = self._get_client()
        
        # Group by index
        by_index: Dict[str, List[StreamEvent]] = {}
        for event in events:
            index_name = self._get_index_name(event.timestamp)
            self._ensure_index(index_name)
            
            if index_name not in by_index:
                by_index[index_name] = []
            by_index[index_name].append(event)
        
        # Build bulk request
        bulk_body = []
        for index_name, index_events in by_index.items():
            for event in index_events:
                bulk_body.append({"index": {"_index": index_name}})
                bulk_body.append(event.to_dict())
        
        try:
            response = client.bulk(body=bulk_body)
            
            errors = [
                item["index"]["error"]
                for item in response.get("items", [])
                if "error" in item.get("index", {})
            ]
            
            if errors:
                return {
                    "status": "partial",
                    "success_count": len(events) - len(errors),
                    "error_count": len(errors),
                    "first_error": str(errors[0]),
                }
            
            return {
                "status": "success",
                "count": len(events),
                "took_ms": response.get("took"),
            }
        
        except Exception as e:
            return {"status": "error", "error": str(e), "count": len(events)}
    
    def query(
        self,
        channel_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[StreamEvent]:
        """Query stored events."""
        client = self._get_client()
        
        # Build query
        must_clauses = []
        
        if channel_id:
            must_clauses.append({"term": {"channel_id": channel_id}})
        
        if workspace_id:
            must_clauses.append({"term": {"context.workspace_id": workspace_id}})
        
        if event_types:
            must_clauses.append({"terms": {"type": event_types}})
        
        if start_time or end_time:
            range_clause = {"range": {"timestamp": {}}}
            if start_time:
                range_clause["range"]["timestamp"]["gte"] = start_time.isoformat()
            if end_time:
                range_clause["range"]["timestamp"]["lte"] = end_time.isoformat()
            must_clauses.append(range_clause)
        
        query = {
            "query": {
                "bool": {"must": must_clauses} if must_clauses else {"match_all": {}}
            },
            "sort": [{"timestamp": "asc"}],
            "size": limit,
        }
        
        try:
            response = client.search(
                index=f"{self.cfg.index_prefix}-*",
                body=query,
            )
            
            events = []
            for hit in response.get("hits", {}).get("hits", []):
                events.append(StreamEvent.from_dict(hit["_source"]))
            
            return events
        
        except Exception:
            return []


class InMemoryEventStorage(EventStorageInterface):
    """
    In-memory event storage for testing/development.
    
    Not suitable for production - events are lost on restart.
    """
    
    def __init__(self, max_events: int = 10000):
        self._events: List[StreamEvent] = []
        self._max_events = max_events
    
    def store(self, event: StreamEvent) -> Dict[str, Any]:
        """Store a single event."""
        self._events.append(event)
        
        # Trim if over limit
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        
        return {"status": "stored", "count": len(self._events)}
    
    def store_batch(self, events: List[StreamEvent]) -> Dict[str, Any]:
        """Store a batch of events."""
        self._events.extend(events)
        
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events:]
        
        return {"status": "stored", "count": len(events)}
    
    def query(
        self,
        channel_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[StreamEvent]:
        """Query stored events."""
        results = []
        
        for event in self._events:
            # Filter by channel_id
            if channel_id and event.channel_id != channel_id:
                continue
            
            # Filter by workspace_id
            if workspace_id and event.context.get("workspace_id") != workspace_id:
                continue
            
            # Filter by event type
            if event_types and event.type not in event_types:
                continue
            
            # Filter by time range
            if event.timestamp:
                try:
                    event_time = datetime.fromisoformat(
                        event.timestamp.replace("Z", "+00:00")
                    )
                    if start_time and event_time < start_time:
                        continue
                    if end_time and event_time > end_time:
                        continue
                except Exception:
                    pass
            
            results.append(event)
            
            if len(results) >= limit:
                break
        
        return results
    
    def clear(self) -> None:
        """Clear all stored events."""
        self._events = []


# Module-level storage singleton
_event_storage: Optional[EventStorageInterface] = None


def init_event_storage(
    config: Optional[EventStorageConfig] = None,
    use_memory: bool = False,
    **kwargs,
) -> EventStorageInterface:
    """
    Initialize the global event storage.
    
    Args:
        config: Storage configuration
        use_memory: Use in-memory storage (testing only)
        **kwargs: Override config fields
        
    Returns:
        Initialized storage instance
    """
    global _event_storage
    
    if use_memory:
        _event_storage = InMemoryEventStorage()
    else:
        if config is None:
            config = EventStorageConfig.from_env()
        
        # Apply kwargs overrides
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)
        
        _event_storage = OpenSearchEventStorage(config)
    
    return _event_storage


def get_event_storage() -> EventStorageInterface:
    """Get the initialized event storage."""
    if _event_storage is None:
        raise RuntimeError("Event storage not initialized. Call init_event_storage() first.")
    return _event_storage


def is_storage_initialized() -> bool:
    """Check if event storage is initialized."""
    return _event_storage is not None
