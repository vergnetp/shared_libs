from __future__ import annotations
"""Anthropic provider."""

from typing import AsyncIterator
import anthropic

# Backend imports (absolute - backend must be in sys.path)
try:
    from resilience import circuit_breaker, with_timeout
except ImportError:
    def circuit_breaker(*args, **kwargs):
        def decorator(fn): return fn
        return decorator
    def with_timeout(*args, **kwargs):
        def decorator(fn): return fn
        return decorator

try:
    from log import info, error
except ImportError:
    def info(msg, **kwargs): pass
    def error(msg, **kwargs): print(f"[ERROR] {msg}")

# Local imports
from ..core import (
    ProviderResponse,
    ProviderError,
    ProviderRateLimitError,
    ProviderAuthError,
)
from .base import LLMProvider
from .utils import parse_anthropic_tool_calls, build_response


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
                        print(f"[DEBUG Anthropic] Skipping orphaned tool_use: {tc_id}")
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
        
        # Convert messages to Anthropic format
        system, chat_messages = self._convert_messages(messages)
        
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
            
            # Extract content
            content = ""
            for block in response.content:
                if block.type == "text":
                    content += block.text
            
            return build_response(
                content=content,
                model=self.model,
                provider=self.name,
                usage={
                    "input": response.usage.input_tokens,
                    "output": response.usage.output_tokens,
                },
                tool_calls=parse_anthropic_tool_calls(response.content),
                finish_reason=response.stop_reason,
                raw=response,
            )
            
        except anthropic.RateLimitError as e:
            raise ProviderRateLimitError(self.name)
        except anthropic.AuthenticationError as e:
            raise ProviderAuthError(self.name)
        except anthropic.BadRequestError as e:
            import json
            error("Anthropic BadRequestError", error=str(e))
            print(f"[ERROR Anthropic] BadRequestError: {e}")
            print(f"[ERROR Anthropic] Messages sent: {json.dumps(chat_messages, indent=2, default=str)}")
            raise ProviderError(self.name, f"Bad request: {e}")
        except anthropic.APIError as e:
            import json
            error("Anthropic API error", error=str(e))
            print(f"[ERROR Anthropic] APIError: {e}")
            print(f"[ERROR Anthropic] Messages sent: {json.dumps(chat_messages, indent=2, default=str)}")
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
