"""
Ollama LLM client.

Local LLM inference via Ollama server. No API key required.

Usage:
    # Async
    from cloud.llm import AsyncOllamaClient
    
    async with AsyncOllamaClient(model="llama3.2") as client:
        response = await client.chat([{"role": "user", "content": "Hello!"}])
        print(response.content)
        
        # Streaming
        async for chunk in client.chat_stream([{"role": "user", "content": "Hello!"}]):
            print(chunk, end="", flush=True)
    
    # Model management
    if await client.is_available():
        models = await client.list_models()
        if "llama3.2" not in models:
            async for progress in client.pull_model("llama3.2"):
                print(f"{progress['status']}: {progress.get('completed', 0)}/{progress.get('total', 0)}")
    
    # Sync
    from cloud.llm import OllamaClient
    
    client = OllamaClient(model="llama3.2")
    response = client.chat([{"role": "user", "content": "Hello!"}])

Note: Unlike other LLM clients, Ollama doesn't require an API key.
"""

from __future__ import annotations
from typing import Dict, Any, Optional, List, AsyncIterator, Iterator
from dataclasses import dataclass
import json

from ...http_client import (
    HttpConfig,
    RetryConfig,
    CircuitBreakerConfig,
    SyncHttpClient,
    AsyncHttpClient,
    get_pooled_sync_client,
    get_pooled_client,
    HttpError,
)
from .types import ChatResponse, ChatMessage, ToolCall
from .errors import (
    LLMError,
    LLMConnectionError,
    LLMTimeoutError,
)


# Default Ollama endpoint
DEFAULT_BASE_URL = "http://localhost:11434"

# Recommended models for RAG (small, fast, good quality)
RECOMMENDED_MODELS = {
    "qwen2.5:3b": {
        "size": "1.9GB",
        "languages": "multilingual",
        "description": "Best quality/size ratio",
    },
    "llama3.2:3b": {
        "size": "2.0GB",
        "languages": "English",
        "description": "Fast, good for English",
    },
}

DEFAULT_MODEL = "qwen2.5:3b"

# Aliases for external imports (used by __init__.py)
OLLAMA_DEFAULT_MODEL = DEFAULT_MODEL
OLLAMA_RECOMMENDED_MODELS = RECOMMENDED_MODELS

# Model context limits (varies by model)
MODEL_LIMITS = {
    "llama3.2": 128000,
    "llama3.1": 128000,
    "mistral": 32768,
    "mixtral": 32768,
    "codellama": 16384,
    "phi3": 128000,
    "qwen2.5": 32768,
}


@dataclass
class OllamaClientConfig:
    """Configuration for Ollama clients."""
    timeout: float = 300.0  # Local models can be slow to start
    connect_timeout: float = 5.0  # Quick check if Ollama is running
    # Retry/circuit breaker disabled by default for local service
    max_retries: int = 0
    circuit_breaker_enabled: bool = False


def _make_ollama_http_config(config: OllamaClientConfig = None) -> HttpConfig:
    """Create HttpConfig with Ollama-appropriate defaults."""
    config = config or OllamaClientConfig()
    
    cb_config = None
    if config.circuit_breaker_enabled:
        cb_config = CircuitBreakerConfig(
            enabled=True,
            failure_threshold=3,
            recovery_timeout=30.0,
        )
    
    return HttpConfig(
        timeout=config.timeout,
        connect_timeout=config.connect_timeout,
        retry=RetryConfig(
            max_retries=config.max_retries,
            base_delay=0.5,
            retry_on_status={500, 502, 503, 504},
        ),
        circuit_breaker=cb_config,
    )


class OllamaClient:
    """
    Sync Ollama client for local LLM inference.
    
    No API key required. Connects to localhost:11434 by default.
    
    Features:
        - Chat completion with tools support
        - Streaming responses
        - Model management (list, pull, check availability)
        - Token counting (estimate)
    """
    
    PROVIDER = "ollama"
    
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = None,
        timeout: float = 300.0,
    ):
        """
        Initialize Ollama client.
        
        Args:
            model: Model name (e.g., "llama3.2", "qwen2.5:3b")
            base_url: Ollama server URL (default: http://localhost:11434)
            timeout: Request timeout in seconds (default: 300s for slow models)
        """
        self.model = model
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        
        # Create config
        config = OllamaClientConfig(timeout=timeout)
        self._http_config = _make_ollama_http_config(config)
        
        # Get pooled client
        self._client: SyncHttpClient = get_pooled_sync_client(
            self._base_url,
            self._http_config,
        )
    
    def is_available(self) -> bool:
        """Check if Ollama server is running."""
        try:
            response = self._client.get("/api/tags", timeout=2.0, raise_on_error=False)
            return response.status_code == 200
        except Exception:
            return False
    
    def list_models(self) -> List[str]:
        """List installed models."""
        try:
            response = self._client.get("/api/tags", timeout=5.0, raise_on_error=False)
            if response.ok:
                data = response.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return []
    
    def has_model(self, model: str = None) -> bool:
        """Check if a model is installed."""
        model = model or self.model
        models = self.list_models()
        base_name = model.split(":")[0]
        return any(model == m or m.startswith(f"{base_name}:") for m in models)
    
    def pull_model(
        self,
        model: str = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        Pull (download) a model with progress streaming.
        
        Args:
            model: Model name (default: client's model)
            
        Yields:
            Progress dicts with 'status', 'completed', 'total' fields
            
        Example:
            for progress in client.pull_model("llama3.2"):
                pct = progress.get("completed", 0) / max(progress.get("total", 1), 1) * 100
                print(f"{progress['status']}: {pct:.1f}%")
        """
        model = model or self.model
        
        for obj in self._client.stream_ndjson(
            "POST",
            "/api/pull",
            json_body={"name": model},
            timeout=None,  # No timeout for downloads
        ):
            yield obj
    
    def ensure_model(self, model: str = None) -> bool:
        """
        Ensure a model is available, pulling if needed.
        
        Returns:
            True if model is available
        """
        model = model or self.model
        
        if not self.is_available():
            return False
        
        if self.has_model(model):
            return True
        
        # Pull model
        try:
            for _ in self.pull_model(model):
                pass  # Consume the stream
            return self.has_model(model)
        except Exception:
            return False
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: List[Dict] = None,
        **kwargs,
    ) -> ChatResponse:
        """
        Chat completion.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Max tokens to generate
            tools: Tool definitions (some models support this)
            **kwargs: Additional Ollama options
            
        Returns:
            ChatResponse with content, tokens, etc.
        """
        request_body = {
            "model": self.model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "stream": False,
        }
        
        if tools:
            request_body["tools"] = tools
        
        try:
            response = self._client.post(
                "/api/chat",
                json=request_body,
                timeout=self.timeout,
                raise_on_error=False,
            )
            
            if not response.ok:
                self._handle_error(response.status_code, response.json_or_none(), response.text)
            
            data = response.json()
            return self._parse_response(data)
            
        except HttpError as e:
            raise LLMConnectionError(str(e), provider=self.PROVIDER)
        except Exception as e:
            raise LLMError(str(e), provider=self.PROVIDER)
    
    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> Iterator[str]:
        """
        Stream chat completion.
        
        Yields:
            Text chunks as they arrive
        """
        request_body = {
            "model": self.model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "stream": True,
        }
        
        for obj in self._client.stream_ndjson(
            "POST",
            "/api/chat",
            json_body=request_body,
            timeout=self.timeout,
        ):
            if "message" in obj and "content" in obj["message"]:
                yield obj["message"]["content"]
    
    def count_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Rough token estimate (Ollama doesn't expose tokenizer)."""
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        """Max context window for this model."""
        return MODEL_LIMITS.get(self.model.split(":")[0], 8192)
    
    def _parse_response(self, data: Dict[str, Any]) -> ChatResponse:
        """Parse Ollama response into ChatResponse."""
        message = data.get("message", {})
        content = message.get("content", "")
        
        # Parse tool calls
        tool_calls = None
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=tc.get("function", {}).get("name", ""),
                    arguments=tc.get("function", {}).get("arguments", {}),
                )
                for i, tc in enumerate(raw_tool_calls)
            ]
        
        # Check for XML-style tool calls in content (Llama sometimes does this)
        if content and not tool_calls and "<function=" in content:
            content, xml_tool_calls = self._parse_xml_tool_calls(content)
            if xml_tool_calls:
                tool_calls = xml_tool_calls
        
        return ChatResponse(
            content=content,
            model=self.model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            tool_calls=tool_calls,
            finish_reason="stop",
            raw=data,
        )
    
    def _parse_xml_tool_calls(self, content: str) -> tuple[str, List[ToolCall]]:
        """Parse XML-style tool calls from content."""
        import re
        
        tool_calls = []
        pattern = r'<function=(\w+)>(.*?)</function>'
        matches = re.findall(pattern, content, re.DOTALL)
        
        for name, args_str in matches:
            try:
                args = json.loads(args_str.strip())
            except json.JSONDecodeError:
                args = {"raw": args_str.strip()}
            
            tool_calls.append(ToolCall(
                id=f"call_{name}_{len(tool_calls)}",
                name=name,
                arguments=args,
            ))
        
        # Remove tool call XML from content
        cleaned = re.sub(pattern, '', content, flags=re.DOTALL).strip()
        
        return cleaned, tool_calls
    
    def _handle_error(self, status_code: int, body: Dict[str, Any], text: str):
        """Convert HTTP errors to LLM errors."""
        error_msg = text[:500] if text else "Unknown error"
        if body and "error" in body:
            error_msg = body["error"]
        
        raise LLMError(error_msg, provider=self.PROVIDER, status_code=status_code, response_body=body)
    
    def close(self) -> None:
        """No-op. Connection pool managed globally."""
        pass
    
    def __enter__(self) -> "OllamaClient":
        return self
    
    def __exit__(self, *args) -> None:
        pass


class AsyncOllamaClient:
    """
    Async Ollama client for local LLM inference.
    
    No API key required. Connects to localhost:11434 by default.
    
    Features:
        - Chat completion with tools support
        - Streaming responses
        - Model management (list, pull, check availability)
        - Token counting (estimate)
    """
    
    PROVIDER = "ollama"
    
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = None,
        timeout: float = 300.0,
    ):
        """
        Initialize async Ollama client.
        
        Args:
            model: Model name (e.g., "llama3.2", "qwen2.5:3b")
            base_url: Ollama server URL (default: http://localhost:11434)
            timeout: Request timeout in seconds (default: 300s for slow models)
        """
        self.model = model
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        
        # Create config
        config = OllamaClientConfig(timeout=timeout)
        self._http_config = _make_ollama_http_config(config)
        self._client: Optional[AsyncHttpClient] = None
    
    async def _ensure_client(self) -> AsyncHttpClient:
        """Lazily initialize pooled client."""
        if self._client is None:
            self._client = await get_pooled_client(
                self._base_url,
                self._http_config,
            )
        return self._client
    
    async def is_available(self) -> bool:
        """Check if Ollama server is running."""
        try:
            client = await self._ensure_client()
            response = await client.get("/api/tags", timeout=2.0, raise_on_error=False)
            return response.status_code == 200
        except Exception:
            return False
    
    async def list_models(self) -> List[str]:
        """List installed models."""
        try:
            client = await self._ensure_client()
            response = await client.get("/api/tags", timeout=5.0, raise_on_error=False)
            if response.ok:
                data = response.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            pass
        return []
    
    async def has_model(self, model: str = None) -> bool:
        """Check if a model is installed."""
        model = model or self.model
        models = await self.list_models()
        base_name = model.split(":")[0]
        return any(model == m or m.startswith(f"{base_name}:") for m in models)
    
    async def pull_model(
        self,
        model: str = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Pull (download) a model with progress streaming.
        
        Args:
            model: Model name (default: client's model)
            
        Yields:
            Progress dicts with 'status', 'completed', 'total' fields
            
        Example:
            async for progress in client.pull_model("llama3.2"):
                pct = progress.get("completed", 0) / max(progress.get("total", 1), 1) * 100
                print(f"{progress['status']}: {pct:.1f}%")
        """
        model = model or self.model
        client = await self._ensure_client()
        
        async for obj in client.stream_ndjson(
            "POST",
            "/api/pull",
            json_body={"name": model},
            timeout=None,  # No timeout for downloads
        ):
            yield obj
    
    async def ensure_model(self, model: str = None) -> bool:
        """
        Ensure a model is available, pulling if needed.
        
        Returns:
            True if model is available
        """
        model = model or self.model
        
        if not await self.is_available():
            return False
        
        if await self.has_model(model):
            return True
        
        # Pull model
        try:
            async for _ in self.pull_model(model):
                pass  # Consume the stream
            return await self.has_model(model)
        except Exception:
            return False
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: List[Dict] = None,
        **kwargs,
    ) -> ChatResponse:
        """
        Chat completion.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-1)
            max_tokens: Max tokens to generate
            tools: Tool definitions (some models support this)
            **kwargs: Additional Ollama options
            
        Returns:
            ChatResponse with content, tokens, etc.
        """
        request_body = {
            "model": self.model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "stream": False,
        }
        
        if tools:
            request_body["tools"] = tools
        
        try:
            client = await self._ensure_client()
            response = await client.post(
                "/api/chat",
                json=request_body,
                timeout=self.timeout,
                raise_on_error=False,
            )
            
            if not response.ok:
                self._handle_error(response.status_code, response.json_or_none(), response.text)
            
            data = response.json()
            return self._parse_response(data)
            
        except HttpError as e:
            raise LLMConnectionError(str(e), provider=self.PROVIDER)
        except Exception as e:
            raise LLMError(str(e), provider=self.PROVIDER)
    
    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream chat completion.
        
        Yields:
            Text chunks as they arrive
        """
        request_body = {
            "model": self.model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "stream": True,
        }
        
        client = await self._ensure_client()
        
        async for obj in client.stream_ndjson(
            "POST",
            "/api/chat",
            json_body=request_body,
            timeout=self.timeout,
        ):
            if "message" in obj and "content" in obj["message"]:
                yield obj["message"]["content"]
    
    def count_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Rough token estimate (Ollama doesn't expose tokenizer)."""
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        """Max context window for this model."""
        return MODEL_LIMITS.get(self.model.split(":")[0], 8192)
    
    def _parse_response(self, data: Dict[str, Any]) -> ChatResponse:
        """Parse Ollama response into ChatResponse."""
        message = data.get("message", {})
        content = message.get("content", "")
        
        # Parse tool calls
        tool_calls = None
        raw_tool_calls = message.get("tool_calls")
        if raw_tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.get("id", f"call_{i}"),
                    name=tc.get("function", {}).get("name", ""),
                    arguments=tc.get("function", {}).get("arguments", {}),
                )
                for i, tc in enumerate(raw_tool_calls)
            ]
        
        # Check for XML-style tool calls in content (Llama sometimes does this)
        if content and not tool_calls and "<function=" in content:
            content, xml_tool_calls = self._parse_xml_tool_calls(content)
            if xml_tool_calls:
                tool_calls = xml_tool_calls
        
        return ChatResponse(
            content=content,
            model=self.model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            tool_calls=tool_calls,
            finish_reason="stop",
            raw=data,
        )
    
    def _parse_xml_tool_calls(self, content: str) -> tuple[str, List[ToolCall]]:
        """Parse XML-style tool calls from content."""
        import re
        
        tool_calls = []
        pattern = r'<function=(\w+)>(.*?)</function>'
        matches = re.findall(pattern, content, re.DOTALL)
        
        for name, args_str in matches:
            try:
                args = json.loads(args_str.strip())
            except json.JSONDecodeError:
                args = {"raw": args_str.strip()}
            
            tool_calls.append(ToolCall(
                id=f"call_{name}_{len(tool_calls)}",
                name=name,
                arguments=args,
            ))
        
        # Remove tool call XML from content
        cleaned = re.sub(pattern, '', content, flags=re.DOTALL).strip()
        
        return cleaned, tool_calls
    
    def _handle_error(self, status_code: int, body: Dict[str, Any], text: str):
        """Convert HTTP errors to LLM errors."""
        error_msg = text[:500] if text else "Unknown error"
        if body and "error" in body:
            error_msg = body["error"]
        
        raise LLMError(error_msg, provider=self.PROVIDER, status_code=status_code, response_body=body)
    
    async def close(self) -> None:
        """No-op. Connection pool managed globally."""
        pass
    
    async def __aenter__(self) -> "AsyncOllamaClient":
        return self
    
    async def __aexit__(self, *args) -> None:
        pass


# Convenience functions for quick access
def get_recommended_models() -> Dict[str, Dict[str, str]]:
    """Get recommended models for RAG."""
    return RECOMMENDED_MODELS


def get_default_model() -> str:
    """Get default model name."""
    return DEFAULT_MODEL
