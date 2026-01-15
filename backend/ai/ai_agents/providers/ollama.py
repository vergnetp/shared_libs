from __future__ import annotations
"""Ollama provider for local models."""

from typing import AsyncIterator
import httpx

# Backend imports (relative)
try:
    from ....resilience import circuit_breaker, with_timeout
except ImportError:
    def circuit_breaker(*args, **kwargs):
        def decorator(fn): return fn
        return decorator
    def with_timeout(*args, **kwargs):
        def decorator(fn): return fn
        return decorator

try:
    from ....log import info, error
except ImportError:
    def info(msg, **kwargs): pass
    def error(msg, **kwargs): print(f"[ERROR] {msg}")

# Local imports
from ..core import ProviderResponse, ProviderError
from .base import LLMProvider
from .utils import parse_ollama_tool_calls, build_response


class OllamaProvider(LLMProvider):
    """Ollama provider for local models."""
    
    name = "ollama"
    
    def __init__(self, model: str = "llama3.1", base_url: str = "http://localhost:11434", api_key: str = None, **kwargs):
        """Initialize Ollama provider.
        
        Args:
            model: Model name (e.g., "llama3.2", "mistral")
            base_url: Ollama server URL (default: localhost:11434)
            api_key: Ignored - Ollama doesn't need authentication
            **kwargs: Ignored extra arguments for compatibility
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=300)
    
    @circuit_breaker(name="ollama")
    @with_timeout(seconds=300)
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        info("Calling Ollama", model=self.model, message_count=len(messages))
        print(f"[DEBUG Ollama] Calling {self.base_url}/api/chat with model={self.model}", flush=True)
        
        try:
            # Convert tools to Ollama format if provided
            request_body = {
                "model": self.model,
                "messages": messages,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
                "stream": False,
            }
            
            # Add tools if provided (Ollama supports tools for some models)
            if tools:
                request_body["tools"] = tools
                print(f"[DEBUG Ollama] Sending {len(tools)} tools", flush=True)
            
            print(f"[DEBUG Ollama] Sending request...", flush=True)
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=request_body,
            )
            print(f"[DEBUG Ollama] Got response status={response.status_code}", flush=True)
            response.raise_for_status()
            data = response.json()
            
            # Ollama returns tokens in eval_count / prompt_eval_count
            input_tokens = data.get("prompt_eval_count", 0)
            output_tokens = data.get("eval_count", 0)
            
            info("Ollama response", input_tokens=input_tokens, output_tokens=output_tokens)
            print(f"[DEBUG Ollama] Success: {output_tokens} tokens generated", flush=True)
            
            # Check for tool calls in response
            message = data.get("message", {})
            content = message.get("content", "")
            
            tool_calls = parse_ollama_tool_calls(message.get("tool_calls"))
            if tool_calls:
                print(f"[DEBUG Ollama] Got {len(tool_calls)} tool calls", flush=True)
            
            # Check for XML-style tool calls in content (Llama sometimes does this)
            if content and not tool_calls and "<function=" in content:
                from .groq import _parse_xml_tool_calls
                content, xml_tool_calls = _parse_xml_tool_calls(content)
                if xml_tool_calls:
                    tool_calls = xml_tool_calls
                    print(f"[DEBUG Ollama] Parsed {len(tool_calls)} XML tool calls", flush=True)
            
            return build_response(
                content=content,
                model=self.model,
                provider=self.name,
                usage={"input": input_tokens, "output": output_tokens},
                tool_calls=tool_calls,
                finish_reason="stop",
                raw=data,
            )
            
        except httpx.HTTPStatusError as e:
            error("Ollama HTTP error", status=e.response.status_code)
            print(f"[ERROR Ollama] HTTP {e.response.status_code}: {e.response.text}", flush=True)
            raise ProviderError(self.name, f"HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            error("Ollama request error", error=str(e))
            print(f"[ERROR Ollama] Request error: {e}", flush=True)
            raise ProviderError(self.name, str(e))
        except Exception as e:
            print(f"[ERROR Ollama] Unexpected error: {type(e).__name__}: {e}", flush=True)
            raise
    
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        async with self.client.stream(
            "POST",
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
                "stream": True,
            },
        ) as response:
            import json
            async for line in response.aiter_lines():
                if line:
                    data = json.loads(line)
                    if "message" in data and "content" in data["message"]:
                        yield data["message"]["content"]
    
    def count_tokens(self, messages: list[dict]) -> int:
        # Rough estimate
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        # Varies by model, default to reasonable limit
        return 8192
