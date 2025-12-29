"""Pre-built tools for ai_agents integration."""

from typing import List, Dict, Any, Optional


def create_rag_tools(searcher, tool_decorator):
    """
    Create RAG tools for use with ai_agents.
    
    Usage:
        from ai_agents import tool
        from rag import RAGSearcher, create_rag_tools
        
        searcher = RAGSearcher(...)
        tools = create_rag_tools(searcher, tool)
        
        agent = Agent(
            role="Property assistant",
            tools=tools,
        )
    
    Args:
        searcher: RAGSearcher instance
        tool_decorator: The @tool decorator from ai_agents
        
    Returns:
        List of tool-decorated functions
    """
    
    @tool_decorator(description="Search documents for relevant information")
    async def search_documents(
        query: str,
        entity_id: str = None,
        top_k: int = 5,
    ) -> str:
        """
        Search for documents matching a query.
        
        Args:
            query: What to search for
            entity_id: Optional filter by entity (e.g., property ID)
            top_k: Number of results to return
            
        Returns:
            Formatted search results
        """
        results = await searcher.search_only(query, top_k=top_k, entity_id=entity_id)
        
        if not results:
            return "No relevant documents found."
        
        output = []
        for i, doc in enumerate(results, 1):
            source = doc.get("source", "Unknown")
            page = doc.get("page", "")
            page_str = f", p.{page}" if page else ""
            content = doc.get("content", "")[:500]
            score = doc.get("score", 0)
            
            output.append(f"{i}. [{source}{page_str}] (score: {score:.2f})\n{content}")
        
        return "\n\n".join(output)
    
    @tool_decorator(description="Ask a question and get an answer from documents")
    async def ask_documents(
        question: str,
        entity_id: str = None,
    ) -> str:
        """
        Ask a question and get an answer based on documents.
        
        Args:
            question: The question to answer
            entity_id: Optional filter by entity
            
        Returns:
            Answer with sources
        """
        result = await searcher.ask(question, entity_id=entity_id)
        
        # Format response
        output = result.answer
        
        if result.sources:
            output += "\n\nSources:"
            for src in result.sources[:3]:
                page_str = f", p.{src['page']}" if src.get('page') else ""
                output += f"\n- {src['filename']}{page_str}"
        
        return output
    
    @tool_decorator(description="Find specific information in a document")
    async def find_in_document(
        query: str,
        filename: str,
    ) -> str:
        """
        Search within a specific document.
        
        Args:
            query: What to find
            filename: Name of the document to search in
            
        Returns:
            Matching excerpts from the document
        """
        # Search with filename filter
        results = await searcher.search(
            query=query,
            filters={"filename": filename},
            top_k=3,
        )
        
        if not results.documents:
            return f"No matches found in '{filename}'."
        
        output = [f"Found in '{filename}':"]
        for doc in results.documents:
            page = doc.get("metadata", {}).get("page_num", "")
            page_str = f" (p.{page})" if page else ""
            content = doc.get("content", "")[:400]
            output.append(f"\n{page_str}: {content}")
        
        return "\n".join(output)
    
    return [search_documents, ask_documents, find_in_document]


def create_simple_search_tool(searcher, tool_decorator):
    """
    Create a single simple search tool.
    
    For agents that just need basic search without full Q&A.
    """
    
    @tool_decorator(description="Search knowledge base for information")
    async def search(query: str, max_results: int = 3) -> str:
        """
        Search the knowledge base.
        
        Args:
            query: Search query
            max_results: Maximum results to return
        """
        results = await searcher.search_only(query, top_k=max_results)
        
        if not results:
            return "No relevant information found."
        
        output = []
        for doc in results:
            source = doc.get("source", "")
            content = doc.get("content", "")[:300]
            output.append(f"[{source}] {content}")
        
        return "\n\n".join(output)
    
    return search
