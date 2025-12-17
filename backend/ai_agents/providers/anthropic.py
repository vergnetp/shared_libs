"""Anthropic provider."""

from typing import AsyncIterator
import anthropic

from shared_lib.resilience import circuit_breaker, with_timeout
from shared_lib.logging import info, error

from ..core import (
    ProviderResponse,
    ProviderError,
    ProviderRateLimitError,
    ProviderAuthError,
)
from .base import LLMProvider


# Model context limits
MODEL_LIMITS = {
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-haiku-3-20240307": 200000,
    "claude-3-5-sonnet-20241022": 200000,
}


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider."""
    
    name = "anthropic"
    
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
    
    @circuit_breaker(name="anthropic")
    @with_timeout(seconds=120)
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        info("Calling Anthropic", model=self.model, message_count=len(messages))
        
        # Extract system message
        system = None
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                chat_messages.append(m)
        
        try:
            params = {
                "model": self.model,
                "messages": chat_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if system:
                params["system"] = system
            if tools:
                params["tools"] = self._convert_tools(tools)
            
            response = await self.client.messages.create(**params)
            
            info("Anthropic response",
                 input_tokens=response.usage.input_tokens,
                 output_tokens=response.usage.output_tokens)
            
            # Extract content and tool calls
            content = ""
            tool_calls = []
            for block in response.content:
                if block.type == "text":
                    content += block.text
                elif block.type == "tool_use":
                    tool_calls.append({
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input,
                    })
            
            return ProviderResponse(
                content=content,
                usage={
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                },
                model=self.model,
                provider=self.name,
                tool_calls=tool_calls,
                finish_reason=response.stop_reason,
                raw=response,
            )
            
        except anthropic.RateLimitError as e:
            raise ProviderRateLimitError(self.name)
        except anthropic.AuthenticationError as e:
            raise ProviderAuthError(self.name)
        except anthropic.APIError as e:
            error("Anthropic API error", error=str(e))
            raise ProviderError(self.name, str(e))
    
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        system = None
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                chat_messages.append(m)
        
        params = {
            "model": self.model,
            "messages": chat_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            params["system"] = system
        if tools:
            params["tools"] = self._convert_tools(tools)
        
        async with self.client.messages.stream(**params) as stream:
            async for text in stream.text_stream:
                yield text
    
    def count_tokens(self, messages: list[dict]) -> int:
        # Rough estimate: 4 chars per token
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        return MODEL_LIMITS.get(self.model, 200000)
    
    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        """Convert generic tool format to Anthropic format."""
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]
