from __future__ import annotations
"""
Ollama provider for local models.

Uses httpx directly since Ollama is a local service (no authentication,
no rate limits). The http_client pool is not needed for localhost.
"""

from typing import AsyncIterator
import json

import httpx

# Local imports
from ..core import ProviderResponse, ProviderError
from .base import LLMProvider
from .utils import parse_ollama_tool_calls, build_response


# Model context limits (varies by model)
MODEL_LIMITS = {
    "llama3.2": 128000,
    "llama3.1": 128000,
    "mistral": 32768,
    "mixtral": 32768,
    "codellama": 16384,
    "phi3": 128000,
}


class OllamaProvider(LLMProvider):
    """
    Ollama provider for local models.
    
    Connects to a local Ollama server (default: http://localhost:11434).
    No authentication required.
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
        self._client = httpx.AsyncClient(timeout=timeout)
    
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
            
            response = await self._client.post(
                f"{self.base_url}/api/chat",
                json=request_body,
            )
            response.raise_for_status()
            data = response.json()
            
            # Ollama returns tokens in eval_count / prompt_eval_count
            input_tokens = data.get("prompt_eval_count", 0)
            output_tokens = data.get("eval_count", 0)
            
            # Check for tool calls in response
            message = data.get("message", {})
            content = message.get("content", "")
            
            tool_calls = parse_ollama_tool_calls(message.get("tool_calls"))
            
            # Check for XML-style tool calls in content (Llama sometimes does this)
            if content and not tool_calls and "<function=" in content:
                content, xml_tool_calls = self._parse_xml_tool_calls(content)
                if xml_tool_calls:
                    tool_calls = xml_tool_calls
            
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
            raise ProviderError(self.name, f"HTTP {e.response.status_code}: {e.response.text[:200]}")
        except httpx.RequestError as e:
            raise ProviderError(self.name, f"Connection error: {e}")
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
        async with self._client.stream(
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
            async for line in response.aiter_lines():
                if line:
                    try:
                        data = json.loads(line)
                        if "message" in data and "content" in data["message"]:
                            yield data["message"]["content"]
                    except json.JSONDecodeError:
                        continue
    
    def count_tokens(self, messages: list[dict]) -> int:
        """Rough token estimate (Ollama doesn't expose tokenizer)."""
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        """Max context window for this model."""
        return MODEL_LIMITS.get(self.model.split(":")[0], 8192)
    
    def _parse_xml_tool_calls(self, content: str) -> tuple[str, list[dict]]:
        """
        Parse XML-style tool calls from content.
        
        Llama models sometimes output tool calls as:
        <function=get_weather>{"location": "NYC"}</function>
        
        Returns:
            Tuple of (cleaned content, list of tool calls)
        """
        import re
        
        tool_calls = []
        pattern = r'<function=(\w+)>(.*?)</function>'
        matches = re.findall(pattern, content, re.DOTALL)
        
        for name, args_str in matches:
            try:
                args = json.loads(args_str.strip())
            except json.JSONDecodeError:
                args = {"raw": args_str.strip()}
            
            tool_calls.append({
                "id": f"call_{name}_{len(tool_calls)}",
                "name": name,
                "arguments": args,
            })
        
        # Remove tool call XML from content
        cleaned = re.sub(pattern, '', content, flags=re.DOTALL).strip()
        
        return cleaned, tool_calls
    
    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
