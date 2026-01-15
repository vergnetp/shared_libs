"""
OpenAI provider using cloud.llm client.

Wraps AsyncOpenAICompatClient with:
- Token counting via ai.tokens
- ProviderResponse conversion
- Resilience (retries, circuit breaker) handled by cloud.llm

Note: OpenAIAssistantProvider still uses the SDK (complex state management).
"""
from __future__ import annotations

from typing import AsyncIterator

from ..core import ProviderResponse
from .base import LLMProvider
from .utils import build_response


# Model context limits
MODEL_LIMITS = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
}


class OpenAIProvider(LLMProvider):
    """
    OpenAI provider using Chat Completions API.
    
    Uses cloud.llm.AsyncOpenAICompatClient for HTTP handling.
    For Assistants API, use OpenAIAssistantProvider (still SDK-based).
    """
    
    name = "openai"
    
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        """
        Initialize OpenAI provider.
        
        Args:
            api_key: OpenAI API key
            model: Model name (default: gpt-4o)
        """
        from ....cloud.llm import AsyncOpenAICompatClient
        
        self.model = model
        self._client = AsyncOpenAICompatClient(
            api_key=api_key,
            model=model,
            timeout=120.0,
            max_retries=3,
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
            tools: Tool definitions
            **kwargs: Additional params
        """
        # Call cloud.llm client
        response = await self._client.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            model=self.model,
        )
        
        # Convert ChatResponse → ProviderResponse
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
            model=response.model,
            provider=self.name,
            usage={
                "input": response.input_tokens,
                "output": response.output_tokens,
            },
            tool_calls=tool_calls,
            finish_reason=response.finish_reason,
            raw=response.raw,
        )
    
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
        
        Note: Tools not supported in streaming mode.
        
        Yields:
            Text chunks as they arrive
        """
        async for chunk in self._client.chat_stream(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            model=self.model,
        ):
            yield chunk
    
    def count_tokens(self, messages: list[dict]) -> int:
        """
        Count tokens in messages.
        
        Uses ai.tokens for accurate counting if available.
        """
        try:
            from ...tokens import count_tokens
            total = 0
            for m in messages:
                content = m.get("content", "")
                if isinstance(content, str):
                    total += count_tokens(content, model=self.model)
            return total
        except ImportError:
            # Fallback heuristic
            total_chars = sum(len(m.get("content", "")) for m in messages if isinstance(m.get("content"), str))
            return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        """Max context window for this model."""
        return MODEL_LIMITS.get(self.model, 128000)
    
    async def close(self):
        """Close the underlying client."""
        await self._client.close()


# Keep OpenAIAssistantProvider from original - uses SDK for state management
# Import and re-export for backwards compatibility
try:
    from ._openai_assistant import OpenAIAssistantProvider
except ImportError:
    # Will be defined below
    pass
