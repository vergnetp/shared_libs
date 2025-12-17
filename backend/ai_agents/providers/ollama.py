"""Ollama provider for local models."""

from typing import AsyncIterator
import httpx

from shared_lib.resilience import circuit_breaker, with_timeout
from shared_lib.logging import info, error

from ..core import ProviderResponse, ProviderError
from .base import LLMProvider


class OllamaProvider(LLMProvider):
    """Ollama provider for local models."""
    
    name = "ollama"
    
    def __init__(self, model: str = "llama3.1", base_url: str = "http://localhost:11434"):
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
        
        try:
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                    "stream": False,
                },
            )
            response.raise_for_status()
            data = response.json()
            
            # Ollama returns tokens in eval_count / prompt_eval_count
            input_tokens = data.get("prompt_eval_count", 0)
            output_tokens = data.get("eval_count", 0)
            
            info("Ollama response", input_tokens=input_tokens, output_tokens=output_tokens)
            
            return ProviderResponse(
                content=data["message"]["content"],
                usage={"input": input_tokens, "output": output_tokens},
                model=self.model,
                provider=self.name,
                tool_calls=[],  # Ollama tool support varies by model
                finish_reason="stop",
                raw=data,
            )
            
        except httpx.HTTPStatusError as e:
            error("Ollama HTTP error", status=e.response.status_code)
            raise ProviderError(self.name, f"HTTP {e.response.status_code}")
        except httpx.RequestError as e:
            error("Ollama request error", error=str(e))
            raise ProviderError(self.name, str(e))
    
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
