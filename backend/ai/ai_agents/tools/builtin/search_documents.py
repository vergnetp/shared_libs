"""
Search Documents Tool - RAG integration for agents.

This tool allows agents to search their associated documents and provides
source citations that are passed through to the chat response.
"""
from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING
from ..base import Tool, ToolDefinition

if TYPE_CHECKING:
    pass


# Global storage for sources (set per-chat by agent)
_current_sources: list[dict] = []
_document_store = None
_agent_id: Optional[str] = None


def set_document_context(doc_store: Any, agent_id: str):
    """Set the document store and agent context for the current chat."""
    global _document_store, _agent_id, _current_sources
    print(f"[DEBUG set_document_context] Called with agent_id={agent_id}, doc_store={doc_store}")
    _document_store = doc_store
    _agent_id = agent_id
    _current_sources = []  # Reset sources for new chat
    print(f"[DEBUG set_document_context] Globals set: _agent_id={_agent_id}")


def get_sources() -> list[dict]:
    """Get sources accumulated during the current chat."""
    return _current_sources.copy()


def clear_sources():
    """Clear accumulated sources."""
    global _current_sources
    _current_sources = []


class SearchDocumentsTool(Tool):
    """
    Tool for searching documents associated with an agent.
    
    Returns the best matching chunk content for the LLM to use,
    and stashes the source metadata for inclusion in the chat response.
    """
    
    @property
    def name(self) -> str:
        return "search_documents"
    
    @property
    def description(self) -> str:
        return (
            "Search uploaded documents for relevant information. "
            "Use this when the user asks about information that might be in "
            "their documents, contracts, leases, manuals, or other uploaded files."
        )
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant information"
                    },
                },
                "required": ["query"]
            }
        )
    
    async def execute(self, query: str, **kwargs) -> str:
        """
        Execute document search.
        
        Args:
            query: Search query
            
        Returns:
            Text content from the best matching chunk
        """
        global _current_sources
        
        print(f"[DEBUG SearchDocumentsTool] execute called with query='{query}'")
        print(f"[DEBUG SearchDocumentsTool] _document_store={_document_store}")
        print(f"[DEBUG SearchDocumentsTool] _agent_id={_agent_id}")
        
        if _document_store is None:
            return "Document search is not available. No documents have been uploaded."
        
        if _agent_id is None:
            return "Document search is not available. Agent context not set."
        
        try:
            # Check what's in the vector store
            total_chunks = await _document_store.count()
            agent_chunks = await _document_store.count(filters={"entity_id": _agent_id})
            # Count unique documents (not chunks)
            agent_docs = await _document_store.count_unique_documents(filters={"entity_id": _agent_id})
            print(f"[DEBUG SearchDocumentsTool] total_chunks={total_chunks}, agent_chunks={agent_chunks}, agent_docs={agent_docs}")
            
            # Detect metadata queries (asking about document count/list/upload info, not content)
            query_lower = query.lower()
            
            # Patterns that ask about document metadata (count, list, upload time, etc.)
            metadata_patterns = [
                # Count patterns
                "how many document", "number of document", "document count", 
                "total document", "count document", "document quantity",
                "how many files", "number of files", "count files",
                # List patterns
                "list document", "list files", "show document", "show files",
                "what document", "which document", "what files", "which files",
                # Upload/time patterns - these can't be answered by content search
                "upload time", "uploaded when", "when upload", "when was",
                "latest upload", "last upload", "recent upload",
                "upload date", "date upload", "created when", "modified when",
            ]
            
            if any(p in query_lower for p in metadata_patterns):
                print(f"[DEBUG SearchDocumentsTool] Detected metadata query, suggesting list_documents")
                # This is a metadata query - redirect to list_documents
                return (
                    f"This appears to be a metadata query. Use the list_documents tool instead for questions about "
                    f"document titles, filenames, counts, or filtering. This search_documents tool is for searching "
                    f"document CONTENT. There are {agent_docs} document(s) uploaded ({agent_chunks} searchable chunks)."
                )
            
            # Search documents scoped to this agent
            result = await _document_store.search(
                query=query,
                top_k=3,  # Get top 3 for reranking, but only use winner
                filters={"entity_id": _agent_id},  # Use entity_id, not agent_id
            )
            
            print(f"[DEBUG SearchDocumentsTool] search returned {len(result.chunks)} chunks")
            
            if not result.chunks:
                # Try without filter to debug
                result_all = await _document_store.search(query=query, top_k=3)
                print(f"[DEBUG SearchDocumentsTool] search WITHOUT filter returned {len(result_all.chunks)} chunks")
                if result_all.chunks:
                    print(f"[DEBUG SearchDocumentsTool] First chunk metadata keys: {list(result_all.chunks[0].metadata.keys())}")
                    print(f"[DEBUG SearchDocumentsTool] First chunk entity_id: {result_all.chunks[0].metadata.get('entity_id')}")
                    print(f"[DEBUG SearchDocumentsTool] Looking for entity_id={_agent_id}")
                return "No relevant information found in the uploaded documents."
            
            # Take the best match (winner)
            winner = result.chunks[0]
            
            # Stash source info for response (deduplicate by document_id + content hash)
            doc_id = winner.metadata.get("document_id", "")
            source_info = {
                "document_id": doc_id,
                "filename": winner.metadata.get("filename", "Unknown"),
                "page": winner.metadata.get("page"),
                "chunk_preview": winner.content[:300] + "..." if len(winner.content) > 300 else winner.content,
                "score": round(winner.score, 3) if winner.score else None,
                "download_url": f"/documents/agent/{_agent_id}/{doc_id}/download" if doc_id else None,
            }
            
            # Deduplicate: check if we already have this exact source
            is_duplicate = any(
                s.get("document_id") == source_info["document_id"] and
                s.get("chunk_preview") == source_info["chunk_preview"]
                for s in _current_sources
            )
            if not is_duplicate:
                _current_sources.append(source_info)
            
            # Return full content for LLM to use
            source_label = f"[From: {source_info['filename']}"
            if source_info.get('page'):
                source_label += f", page {source_info['page']}"
            source_label += "]"
            
            return f"{source_label}\n\n{winner.content}"
            
        except Exception as e:
            return f"Error searching documents: {str(e)}"


# Create singleton instance
_tool_instance = SearchDocumentsTool()


def get_search_documents_tool() -> SearchDocumentsTool:
    """Get the search_documents tool instance."""
    return _tool_instance
