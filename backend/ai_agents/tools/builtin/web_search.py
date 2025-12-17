"""Web search tool - placeholder implementation."""

from ..base import Tool, ToolDefinition


class WebSearchTool(Tool):
    """
    Web search tool.
    
    This is a placeholder - implement with your preferred search API:
    - SerpAPI
    - Brave Search
    - Bing Search
    - Google Custom Search
    """
    
    name = "web_search"
    description = "Search the web for current information."
    
    def __init__(self, search_fn=None):
        """
        Args:
            search_fn: Async function(query: str, num_results: int) -> list[dict]
                       Each result: {"title": str, "url": str, "snippet": str}
        """
        self.search_fn = search_fn
    
    async def execute(self, query: str, num_results: int = 5) -> str:
        """
        Search the web.
        
        Args:
            query: Search query
            num_results: Number of results to return
            
        Returns:
            Formatted search results
        """
        if self.search_fn is None:
            return "Error: Web search not configured. Provide a search_fn."
        
        results = await self.search_fn(query, num_results)
        
        if not results:
            return "No results found."
        
        output = []
        for i, r in enumerate(results, 1):
            output.append(f"{i}. {r['title']}")
            output.append(f"   URL: {r['url']}")
            output.append(f"   {r['snippet']}")
            output.append("")
        
        return "\n".join(output)
    
    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results (default 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        )
