from __future__ import annotations
"""
Ollama provider for local models.

Uses cloud.llm.AsyncOllamaClient for HTTP operations with proper connection
pooling, tracing, and error handling.
"""

from typing import AsyncIterator

# Local imports
from ..core import ProviderResponse, ProviderError
from .base import LLMProvider
from .utils import build_response

# Cloud LLM client (relative import from ai/ai_agents/providers -> cloud/llm)
from ....cloud.llm import AsyncOllamaClient, LLMError


class OllamaProvider(LLMProvider):
    """
    Ollama provider for local models.
    
    Connects to a local Ollama server (default: http://localhost:11434).
    No authentication required.
    
    Uses cloud.llm.AsyncOllamaClient for:
    - Connection pooling
    - Request tracing
    - Proper error handling
    - NDJSON streaming
    """
    
    name = "ollama"
    
    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        api_key: str = None,  # Ignored - Ollama doesn't need auth
        timeout: float = 300.0,
        **kwargs,
    ):
        """
        Initialize Ollama provider.
        
        Args:
            model: Model name (e.g., "llama3.2", "mistral", "codellama")
            base_url: Ollama server URL (default: localhost:11434)
            api_key: Ignored - Ollama doesn't need authentication
            timeout: Request timeout in seconds (default: 300s for slow models)
            **kwargs: Ignored extra arguments for compatibility
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        
        # Create the async client (uses pooled connections)
        self._client = AsyncOllamaClient(
            model=model,
            base_url=base_url,
            timeout=timeout,
        )
    
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        """
        Run chat completion.
        
        Args:
            messages: List of message dicts
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            tools: Tool definitions (supported by some Ollama models)
            **kwargs: Additional parameters
            
        Returns:
            ProviderResponse with content and usage
        """
        try:
            # Use the cloud.llm client
            response = await self._client.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                **kwargs,
            )
            
            # Convert tool calls from cloud.llm format to provider format
            tool_calls = None
            if response.tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    }
                    for tc in response.tool_calls
                ]
            
            return build_response(
                content=response.content,
                model=self.model,
                provider=self.name,
                usage={"input": response.input_tokens, "output": response.output_tokens},
                tool_calls=tool_calls,
                finish_reason=response.finish_reason,
                raw=response.raw,
            )
            
        except LLMError as e:
            raise ProviderError(self.name, str(e))
        except Exception as e:
            raise ProviderError(self.name, str(e))
    
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream chat completion.
        
        Yields:
            Text chunks as they arrive
        """
        try:
            async for chunk in self._client.chat_stream(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            ):
                yield chunk
        except LLMError as e:
            raise ProviderError(self.name, str(e))
        except Exception as e:
            raise ProviderError(self.name, str(e))
    
    def count_tokens(self, messages: list[dict]) -> int:
        """Rough token estimate (Ollama doesn't expose tokenizer)."""
        return self._client.count_tokens(messages)
    
    @property
    def max_context_tokens(self) -> int:
        """Max context window for this model."""
        return self._client.max_context_tokens
    
    async def close(self):
        """No-op. Connection pool managed globally by cloud.llm."""
        await self._client.close()
