"""
Anthropic Claude provider using cloud.llm client.

Wraps AsyncAnthropicClient with:
- Token counting via ai.tokens
- ProviderResponse conversion
- Resilience (retries, circuit breaker) handled by cloud.llm
"""
from __future__ import annotations

from typing import AsyncIterator

from ..core import ProviderResponse
from .base import LLMProvider
from .utils import build_response


# Model context limits
MODEL_LIMITS = {
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-haiku-3-20240307": 200000,
    "claude-3-5-sonnet-20241022": 200000,
}


def _get_anthropic_client():
    """Import AsyncAnthropicClient from cloud.llm (relative import)."""
    try:
        from ....cloud.llm import AsyncAnthropicClient
        return AsyncAnthropicClient
    except ImportError:
        raise ImportError(
            "cloud.llm module not found. "
            "Ensure shared_libs/backend/cloud/llm is available."
        )


class AnthropicProvider(LLMProvider):
    """
    Anthropic Claude provider.
    
    Uses cloud.llm.AsyncAnthropicClient for HTTP handling.
    """
    
    name = "anthropic"
    
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        """
        Initialize Anthropic provider.
        
        Args:
            api_key: Anthropic API key
            model: Model name (default: claude-sonnet-4-20250514)
        """
        AsyncAnthropicClient = _get_anthropic_client()
        
        self.model = model
        self._client = AsyncAnthropicClient(
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
            **kwargs: Additional params (system, etc.)
        """
        # Extract system from kwargs or messages
        system = kwargs.get("system")
        
        # Call cloud.llm client
        response = await self._client.chat(
            messages=messages,
            system=system,
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
        system = kwargs.get("system")
        
        async for chunk in self._client.chat_stream(
            messages=messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            model=self.model,
        ):
            yield chunk
    
    def count_tokens(self, messages: list[dict]) -> int:
        """
        Count tokens in messages.
        
        Uses ai.tokens for accurate counting if available,
        falls back to heuristic.
        """
        try:
            from ...tokens import estimate_tokens
        except ImportError:
            # Fallback heuristic
            def estimate_tokens(text):
                return len(text) // 4
        
        total = 0
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str):
                total += estimate_tokens(content)
            elif isinstance(content, list):
                # Handle multi-part content
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += estimate_tokens(part.get("text", ""))
        
        return total
    
    @property
    def max_context_tokens(self) -> int:
        """Max context window for this model."""
        return MODEL_LIMITS.get(self.model, 200000)
    
    async def close(self):
        """Close the underlying client."""
        await self._client.close()
