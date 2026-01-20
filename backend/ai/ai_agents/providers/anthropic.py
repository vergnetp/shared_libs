"""Anthropic provider using cloud.llm client."""
from __future__ import annotations

from typing import AsyncIterator

# Lazy import to avoid circular dependencies
def _get_anthropic_client():
    """Lazy import of AsyncAnthropicClient."""
    from ....cloud.llm import AsyncAnthropicClient
    return AsyncAnthropicClient

def _get_llm_errors():
    """Lazy import of LLM error classes."""
    from ....cloud.llm import LLMError, LLMRateLimitError, LLMAuthError
    return LLMError, LLMRateLimitError, LLMAuthError

# Resilience decorators
from ....resilience import circuit_breaker, with_timeout

# Logging
from ....log import info, error

# Local imports
from ..core import (
    ProviderResponse,
    ProviderError,
    ProviderRateLimitError,
    ProviderAuthError,
)
from .base import LLMProvider
from .utils import build_response


# Model context limits
MODEL_LIMITS = {
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-haiku-3-20240307": 200000,
    "claude-3-5-sonnet-20241022": 200000,
}


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider using cloud.llm client."""
    
    name = "anthropic"
    
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.model = model
        self._client = None
    
    async def _get_client(self):
        """Get or create the async client."""
        if self._client is None:
            AsyncAnthropicClient = _get_anthropic_client()
            self._client = AsyncAnthropicClient(api_key=self.api_key, model=self.model)
        return self._client
    
    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Convert messages to Anthropic format.
        
        Returns:
            tuple: (system_message, chat_messages)
        """
        import json
        system = None
        chat_messages = []
        
        # First pass: collect all tool_result IDs so we know which tool_use blocks have results
        tool_result_ids = set()
        for m in messages:
            if m["role"] == "tool" and m.get("tool_call_id"):
                tool_result_ids.add(m["tool_call_id"])
        
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            elif m["role"] == "tool":
                # Convert OpenAI-style tool result to Anthropic format
                chat_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m["tool_call_id"],
                        "content": m["content"],
                    }]
                })
            elif m["role"] == "assistant" and m.get("tool_calls"):
                # Convert assistant message with tool_calls to Anthropic format
                # Only include tool_use blocks that have corresponding tool_results
                content_blocks = []
                if m.get("content"):
                    content_blocks.append({"type": "text", "text": m["content"]})
                
                for tc in m["tool_calls"]:
                    tc_id = tc.get("id") or (tc.get("function", {}).get("id") if "function" in tc else None)
                    
                    # Skip orphaned tool_use (no corresponding tool_result in history)
                    if tc_id and tc_id not in tool_result_ids:
                        continue
                    
                    # Check if OpenAI format (has "function" key) or internal format
                    if "function" in tc:
                        # OpenAI format: {"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}
                        func = tc["function"]
                        args = func.get("arguments", "{}")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except json.JSONDecodeError:
                                args = {}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": func["name"],
                            "input": args,
                        })
                    else:
                        # Internal format: {"id": "...", "name": "...", "arguments": {...}}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["name"],
                            "input": tc.get("arguments", {}),
                        })
                
                # Only add message if we have content
                if content_blocks:
                    chat_messages.append({
                        "role": "assistant",
                        "content": content_blocks,
                    })
                elif m.get("content"):
                    # All tool_use were orphaned, just add text content
                    chat_messages.append({
                        "role": "assistant",
                        "content": m["content"],
                    })
            else:
                chat_messages.append(m)
        
        return system, chat_messages
    
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
    
    def _parse_tool_calls(self, response) -> list[dict]:
        """Parse tool calls from cloud.llm response."""
        if not response.has_tool_calls:
            return []
        
        return [
            {
                "id": tc.id,
                "name": tc.name,
                "arguments": tc.arguments,
            }
            for tc in response.tool_calls
        ]
    
    @circuit_breaker(name="anthropic")
    @with_timeout(120)
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        info("Calling Anthropic", model=self.model, message_count=len(messages))
        
        # Convert messages to Anthropic format
        system, chat_messages = self._convert_messages(messages)
        
        LLMError, LLMRateLimitError, LLMAuthError = _get_llm_errors()
        
        try:
            client = await self._get_client()
            
            # Convert tools to Anthropic format
            anthropic_tools = self._convert_tools(tools) if tools else None
            
            response = await client.chat(
                messages=chat_messages,
                system=system,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=anthropic_tools,
            )
            
            info("Anthropic response",
                 input_tokens=response.usage.get("input", 0) if response.usage else 0,
                 output_tokens=response.usage.get("output", 0) if response.usage else 0)
            
            return build_response(
                content=response.content or "",
                model=self.model,
                provider=self.name,
                usage=response.usage or {"input": 0, "output": 0},
                tool_calls=self._parse_tool_calls(response),
                finish_reason=response.finish_reason,
                raw=response,
            )
            
        except LLMRateLimitError:
            raise ProviderRateLimitError(self.name)
        except LLMAuthError:
            raise ProviderAuthError(self.name)
        except LLMError as e:
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
        # Convert messages to Anthropic format
        system, chat_messages = self._convert_messages(messages)
        
        # Convert tools to Anthropic format
        anthropic_tools = self._convert_tools(tools) if tools else None
        
        client = await self._get_client()
        
        async for chunk in client.chat_stream(
            messages=chat_messages,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=anthropic_tools,
        ):
            yield chunk
    
    def count_tokens(self, messages: list[dict]) -> int:
        # Rough estimate: 4 chars per token
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        return MODEL_LIMITS.get(self.model, 200000)
    
    async def close(self):
        """Close the underlying client."""
        if self._client:
            await self._client.close()
            self._client = None
