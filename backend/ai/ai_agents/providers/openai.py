"""OpenAI provider with Assistants API support."""
from __future__ import annotations

from typing import AsyncIterator
import json
import openai

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
from .utils import parse_openai_tool_calls, build_response


MODEL_LIMITS = {
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
}


class OpenAIProvider(LLMProvider):
    """
    OpenAI provider using Chat Completions API.
    
    Use for custom memory strategies (first_last, summarize, token_window).
    For last_n, use OpenAIAssistantProvider instead.
    """
    
    name = "openai"
    
    def __init__(self, api_key: str, model: str = "gpt-4o"):
        self.client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model
    
    @circuit_breaker(name="openai")
    @with_timeout(seconds=120)
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        info("Calling OpenAI", model=self.model, message_count=len(messages))
        
        try:
            params = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if tools:
                params["tools"] = self._convert_tools(tools)
            
            response = await self.client.chat.completions.create(**params)
            choice = response.choices[0]
            
            return build_response(
                content=choice.message.content,
                model=self.model,
                provider=self.name,
                usage={
                    "input": response.usage.prompt_tokens,
                    "output": response.usage.completion_tokens,
                },
                tool_calls=parse_openai_tool_calls(choice.message.tool_calls),
                finish_reason=choice.finish_reason,
                raw=response,
            )
            
        except openai.RateLimitError:
            raise ProviderRateLimitError(self.name)
        except openai.AuthenticationError:
            raise ProviderAuthError(self.name)
        except openai.BadRequestError as e:
            # Log the full error for debugging
            print(f"[ERROR OpenAI] BadRequestError: {e}")
            print(f"[ERROR OpenAI] Messages sent: {json.dumps(messages, indent=2, default=str)}")
            raise ProviderError(self.name, f"Bad request: {e}")
        except openai.APIError as e:
            print(f"[ERROR OpenAI] APIError: {e}")
            raise ProviderError(self.name, str(e))
    
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            params["tools"] = self._convert_tools(tools)
        
        async for chunk in await self.client.chat.completions.create(**params):
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    
    def count_tokens(self, messages: list[dict]) -> int:
        return sum(len(m.get("content", "")) for m in messages) // 4
    
    @property
    def max_context_tokens(self) -> int:
        return MODEL_LIMITS.get(self.model, 128000)
    
    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        return [{
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            }
        } for t in tools]


class OpenAIAssistantProvider(LLMProvider):
    """
    OpenAI Assistants API provider.
    
    Handles threading and context automatically.
    Use for last_n memory strategy - OpenAI manages the context window.
    """
    
    name = "openai_assistant"
    
    def __init__(self, api_key: str, model: str = "gpt-4o", assistant_id: str = None):
        self.client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model
        self.assistant_id = assistant_id
        self._thread_cache: dict[str, str] = {}  # our_thread_id -> openai_thread_id
    
    async def get_or_create_assistant(
        self,
        name: str,
        instructions: str,
        tools: list[dict] = None,
    ) -> str:
        """Create assistant if not exists. Returns assistant_id."""
        if self.assistant_id:
            return self.assistant_id
        
        openai_tools = []
        if tools:
            for t in tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    }
                })
        
        assistant = await self.client.beta.assistants.create(
            name=name,
            instructions=instructions,
            model=self.model,
            tools=openai_tools or None,
        )
        
        self.assistant_id = assistant.id
        info("Created assistant", assistant_id=assistant.id)
        return assistant.id
    
    async def get_or_create_thread(self, thread_id: str) -> str:
        """Map our thread_id to OpenAI thread. Creates if needed."""
        if thread_id in self._thread_cache:
            return self._thread_cache[thread_id]
        
        thread = await self.client.beta.threads.create()
        self._thread_cache[thread_id] = thread.id
        info("Created OpenAI thread", openai_id=thread.id, our_id=thread_id)
        return thread.id
    
    def set_thread_mapping(self, thread_id: str, openai_thread_id: str):
        """Load existing mapping (from DB)."""
        self._thread_cache[thread_id] = openai_thread_id
    
    @circuit_breaker(name="openai_assistant")
    @with_timeout(seconds=120)
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        thread_id: str = None,
        **kwargs,
    ) -> ProviderResponse:
        """
        Run via Assistants API.
        
        Only the latest user message is sent - OpenAI manages history.
        """
        if not self.assistant_id:
            raise ProviderError(self.name, "Call get_or_create_assistant first")
        
        openai_thread_id = await self.get_or_create_thread(thread_id or "default")
        
        # Extract latest user message
        user_message = None
        for m in reversed(messages):
            if m["role"] == "user":
                user_message = m["content"]
                break
        
        if not user_message:
            raise ProviderError(self.name, "No user message")
        
        info("Calling Assistant", assistant_id=self.assistant_id, thread=openai_thread_id)
        
        try:
            # Add message to thread
            await self.client.beta.threads.messages.create(
                thread_id=openai_thread_id,
                role="user",
                content=user_message,
            )
            
            # Run and poll
            run = await self.client.beta.threads.runs.create_and_poll(
                thread_id=openai_thread_id,
                assistant_id=self.assistant_id,
                temperature=temperature,
                max_completion_tokens=max_tokens,
            )
            
            if run.status == "failed":
                raise ProviderError(self.name, f"Run failed: {run.last_error}")
            
            # Handle tool calls
            if run.status == "requires_action":
                tool_calls = parse_openai_tool_calls(
                    run.required_action.submit_tool_outputs.tool_calls,
                    extra_fields={"_run_id": run.id},  # Need for submit_tool_outputs
                )
                
                return build_response(
                    content="",
                    model=self.model,
                    provider=self.name,
                    usage={"input": run.usage.prompt_tokens if run.usage else 0,
                           "output": run.usage.completion_tokens if run.usage else 0},
                    tool_calls=tool_calls,
                    finish_reason="tool_calls",
                    raw=run,
                )
            
            # Get response
            resp = await self.client.beta.threads.messages.list(
                thread_id=openai_thread_id,
                order="desc",
                limit=1,
            )
            
            content = ""
            for block in resp.data[0].content:
                if block.type == "text":
                    content += block.text.value
            
            return build_response(
                content=content,
                model=self.model,
                provider=self.name,
                usage={"input": run.usage.prompt_tokens if run.usage else 0,
                       "output": run.usage.completion_tokens if run.usage else 0},
                finish_reason="stop",
                raw=run,
            )
            
        except openai.RateLimitError:
            raise ProviderRateLimitError(self.name)
        except openai.AuthenticationError:
            raise ProviderAuthError(self.name)
        except openai.APIError as e:
            raise ProviderError(self.name, str(e))
    
    async def submit_tool_outputs(
        self,
        thread_id: str,
        run_id: str,
        tool_outputs: list[dict],
    ) -> ProviderResponse:
        """Submit tool results and continue."""
        openai_thread_id = self._thread_cache.get(thread_id)
        if not openai_thread_id:
            raise ProviderError(self.name, f"Unknown thread: {thread_id}")
        
        run = await self.client.beta.threads.runs.submit_tool_outputs_and_poll(
            thread_id=openai_thread_id,
            run_id=run_id,
            tool_outputs=[
                {"tool_call_id": to["tool_call_id"], "output": to["output"]}
                for to in tool_outputs
            ],
        )
        
        if run.status == "requires_action":
            tool_calls = parse_openai_tool_calls(
                run.required_action.submit_tool_outputs.tool_calls,
                extra_fields={"_run_id": run.id},
            )
            
            return build_response(
                content="",
                model=self.model,
                provider=self.name,
                tool_calls=tool_calls,
                finish_reason="tool_calls",
                raw=run,
            )
        
        # Final response
        resp = await self.client.beta.threads.messages.list(
            thread_id=openai_thread_id,
            order="desc",
            limit=1,
        )
        
        content = ""
        for block in resp.data[0].content:
            if block.type == "text":
                content += block.text.value
        
        return build_response(
            content=content,
            model=self.model,
            provider=self.name,
            usage={"input": run.usage.prompt_tokens if run.usage else 0,
                   "output": run.usage.completion_tokens if run.usage else 0},
            finish_reason="stop",
            raw=run,
        )
    
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thread_id: str = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        if not self.assistant_id:
            raise ProviderError(self.name, "Call get_or_create_assistant first")
        
        openai_thread_id = await self.get_or_create_thread(thread_id or "default")
        
        user_message = None
        for m in reversed(messages):
            if m["role"] == "user":
                user_message = m["content"]
                break
        
        if not user_message:
            raise ProviderError(self.name, "No user message")
        
        await self.client.beta.threads.messages.create(
            thread_id=openai_thread_id,
            role="user",
            content=user_message,
        )
        
        async with self.client.beta.threads.runs.stream(
            thread_id=openai_thread_id,
            assistant_id=self.assistant_id,
            temperature=temperature,
            max_completion_tokens=max_tokens,
        ) as stream:
            async for text in stream.text_deltas:
                yield text
    
    def count_tokens(self, messages: list[dict]) -> int:
        return sum(len(m.get("content", "")) for m in messages) // 4
    
    @property
    def max_context_tokens(self) -> int:
        return MODEL_LIMITS.get(self.model, 128000)
