"""
List Documents Tool - Query document metadata.

This tool allows agents to query document metadata (titles, filenames, tags, dates)
rather than searching document content. Use this for questions like:
- "How many documents do we have?"
- "List documents with 'contract' in the title"
- "What documents were uploaded recently?"
"""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING
from ..base import Tool, ToolDefinition

# Import shared context from search_documents
from .search_documents import _document_store, _agent_id

if TYPE_CHECKING:
    pass


class ListDocumentsTool(Tool):
    """
    Tool for listing and filtering documents by metadata.
    
    Unlike search_documents (which searches content), this queries
    document metadata: titles, filenames, tags, upload dates.
    """
    
    @property
    def name(self) -> str:
        return "list_documents"
    
    @property
    def description(self) -> str:
        return (
            "List documents by metadata (title, filename, tags). "
            "Use this for questions about document names, counts, or filtering by title/filename patterns. "
            "For searching document CONTENT, use search_documents instead."
        )
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "title_contains": {
                        "type": "string",
                        "description": "Filter documents where title contains this text (case-insensitive)"
                    },
                    "title_starts_with": {
                        "type": "string", 
                        "description": "Filter documents where title starts with this text (case-insensitive)"
                    },
                    "filename_contains": {
                        "type": "string",
                        "description": "Filter documents where filename contains this text (case-insensitive)"
                    },
                    "tag": {
                        "type": "string",
                        "description": "Filter documents that have this tag"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of documents to return (default: 20)"
                    }
                },
                "required": []
            }
        )
    
    async def execute(self, **kwargs) -> str:
        """List documents matching the filter criteria."""
        # Import at runtime to get current values
        from .search_documents import _document_store, _agent_id
        
        if _document_store is None:
            return "No document store available."
        
        if _agent_id is None:
            return "No agent context set."
        
        title_contains = kwargs.get("title_contains", "").lower()
        title_starts_with = kwargs.get("title_starts_with", "").lower()
        filename_contains = kwargs.get("filename_contains", "").lower()
        tag_filter = kwargs.get("tag", "")
        limit = kwargs.get("limit", 20)
        
        try:
            # Get all documents for this agent from the metadata store
            # The document store keeps track of documents in _documents dict
            all_docs = []
            
            # Access internal document registry
            if hasattr(_document_store, '_documents'):
                for doc_id, doc_info in _document_store._documents.items():
                    metadata = doc_info.get("metadata", {})
                    # Check if this doc belongs to our agent
                    if metadata.get("entity_id") == _agent_id or metadata.get("agent_id") == _agent_id:
                        all_docs.append({
                            "id": doc_id,
                            "title": metadata.get("title", ""),
                            "filename": metadata.get("filename", doc_info.get("filename", "")),
                            "tags": metadata.get("tags", []),
                            "size_bytes": metadata.get("size_bytes", 0),
                            "chunk_count": metadata.get("chunk_count", 0),
                            "created_at": metadata.get("created_at", ""),
                        })
            
            # Apply filters
            filtered = []
            for doc in all_docs:
                title = (doc.get("title") or "").lower()
                filename = (doc.get("filename") or "").lower()
                tags = doc.get("tags") or []
                
                # Title contains filter
                if title_contains and title_contains not in title:
                    continue
                
                # Title starts with filter
                if title_starts_with and not title.startswith(title_starts_with):
                    continue
                
                # Filename contains filter
                if filename_contains and filename_contains not in filename:
                    continue
                
                # Tag filter
                if tag_filter and tag_filter not in tags:
                    continue
                
                filtered.append(doc)
            
            # Apply limit
            filtered = filtered[:limit]
            
            # Format response
            if not filtered:
                filter_desc = []
                if title_contains:
                    filter_desc.append(f"title containing '{title_contains}'")
                if title_starts_with:
                    filter_desc.append(f"title starting with '{title_starts_with}'")
                if filename_contains:
                    filter_desc.append(f"filename containing '{filename_contains}'")
                if tag_filter:
                    filter_desc.append(f"tag '{tag_filter}'")
                
                if filter_desc:
                    return f"No documents found matching: {', '.join(filter_desc)}. Total documents: {len(all_docs)}"
                return f"No documents uploaded to this agent."
            
            # Build response
            lines = [f"Found {len(filtered)} document(s)" + (f" (showing first {limit})" if len(all_docs) > limit else "") + ":"]
            for doc in filtered:
                title = doc.get("title") or doc.get("filename") or "Untitled"
                filename = doc.get("filename", "")
                chunks = doc.get("chunk_count", 0)
                tags = doc.get("tags", [])
                
                line = f"- {title}"
                if filename and filename != title:
                    line += f" ({filename})"
                if chunks:
                    line += f" [{chunks} chunks]"
                if tags:
                    line += f" tags: {', '.join(tags)}"
                lines.append(line)
            
            return "\n".join(lines)
            
        except Exception as e:
            print(f"[ERROR ListDocumentsTool] {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            return f"Error listing documents: {str(e)}"
