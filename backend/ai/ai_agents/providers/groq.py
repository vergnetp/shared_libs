"""Groq provider - fast inference with OpenAI-compatible API.

Uses cloud.llm.AsyncOpenAICompatClient for all API calls.
"""
from __future__ import annotations

from typing import AsyncIterator, Optional, TYPE_CHECKING
import json
import re

# Lazy imports for cloud.llm
_openai_compat_client_cls = None
_llm_errors = None

def _get_openai_compat_client():
    """Lazy import of AsyncOpenAICompatClient."""
    global _openai_compat_client_cls
    if _openai_compat_client_cls is None:
        from ....cloud.llm import AsyncOpenAICompatClient
        _openai_compat_client_cls = AsyncOpenAICompatClient
    return _openai_compat_client_cls

def _get_llm_errors():
    """Lazy import of LLM error classes."""
    global _llm_errors
    if _llm_errors is None:
        from ....cloud.llm import LLMError, LLMRateLimitError, LLMAuthError
        _llm_errors = {
            'LLMError': LLMError,
            'LLMRateLimitError': LLMRateLimitError,
            'LLMAuthError': LLMAuthError,
        }
    return _llm_errors

from ....resilience import circuit_breaker, with_timeout
from ....log import info, error

from ..core import (
    ProviderResponse,
    ProviderError,
    ProviderRateLimitError,
    ProviderAuthError,
)
from .base import LLMProvider
from .utils import parse_openai_tool_calls, build_response, parse_tool_args


MODEL_LIMITS = {
    "llama-3.3-70b-versatile": 128000,
    "llama-3.1-8b-instant": 128000,
    "mixtral-8x7b-32768": 32768,
    "gemma2-9b-it": 8192,
}


class GroqProvider(LLMProvider):
    """Groq provider - extremely fast inference.
    
    Uses cloud.llm.AsyncOpenAICompatClient for API calls.
    """
    
    name = "groq"
    
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile", **kwargs):
        self._api_key = api_key
        self.model = model
        self._client: Optional[object] = None
    
    def _get_client(self):
        """Get or create the async client."""
        if self._client is None:
            ClientClass = _get_openai_compat_client()
            self._client = ClientClass(
                api_key=self._api_key,
                base_url="https://api.groq.com/openai/v1",
                model=self.model,
            )
        return self._client
    
    async def close(self):
        """Close the underlying client."""
        if self._client is not None:
            await self._client.close()
            self._client = None
    
    @circuit_breaker(name="groq")
    @with_timeout(60)
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        info("Calling Groq", model=self.model, message_count=len(messages))
        print(f"[DEBUG Groq] Calling model={self.model}, messages={len(messages)}", flush=True)
        
        # Filter out system messages from the list and use as system param
        system = kwargs.get("system")
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                msg = dict(m)
                # Ensure tool_calls are in OpenAI format (Groq requires type: "function")
                if msg.get("tool_calls"):
                    normalized_tc = []
                    for tc in msg["tool_calls"]:
                        if "type" not in tc:
                            # Convert internal format to OpenAI format
                            args = tc.get("arguments") or {}  # Handle None from Llama
                            if isinstance(args, dict):
                                args = json.dumps(args)
                            elif args is None:
                                args = "{}"
                            normalized_tc.append({
                                "id": tc.get("id", f"call_{len(normalized_tc)}"),
                                "type": "function",
                                "function": {
                                    "name": tc.get("name", ""),
                                    "arguments": args,
                                }
                            })
                        else:
                            # Already in OpenAI format, but ensure arguments isn't None
                            if tc.get("function", {}).get("arguments") is None:
                                tc = dict(tc)
                                tc["function"] = dict(tc.get("function", {}))
                                tc["function"]["arguments"] = "{}"
                            normalized_tc.append(tc)
                    msg["tool_calls"] = normalized_tc
                    print(f"[DEBUG Groq] Normalized tool_calls for role={msg['role']}: {normalized_tc[:1]}...", flush=True)
                chat_messages.append(msg)
        
        # Prepend system message if present
        # Add tool format instructions for Llama models when tools are provided
        if system and tools:
            tool_instructions = """## Tool Calling Format

You have access to tools. When you need to call a tool, use this EXACT format:

<function=TOOL_NAME>{"arg1": "value1", "arg2": "value2"}</function>

Example - to remember the user's name:
<function=update_context>{"updates": {"name": "Phil"}, "reason": "User shared their name"}</function>

IMPORTANT:
- Use <function=NAME> with equals sign, not colon, slash, or parentheses
- Put valid JSON immediately after the >
- Close with </function>
- You can include normal text before or after the function call

---

"""
            system = tool_instructions + system
        
        if system:
            chat_messages = [{"role": "system", "content": system}] + chat_messages
        
        llm_errors = _get_llm_errors()
        
        try:
            client = self._get_client()
            
            print(f"[DEBUG Groq] Sending {len(tools) if tools else 0} tools", flush=True)
            
            response = await client.chat(
                messages=chat_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                tool_choice="auto" if tools else None,
            )
            
            content = response.content or ""
            
            # Extract tool calls from cloud.llm response
            tool_calls = []
            if response.has_tool_calls:
                for tc in response.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                    })
                print(f"[DEBUG Groq] Got {len(tool_calls)} tool calls from API", flush=True)
            
            # Note: XML-style tool calls in content are now handled at the Agent level
            # in agent.py _completion_loop, so we don't parse them here
            
            info("Groq response",
                 input_tokens=response.input_tokens,
                 output_tokens=response.output_tokens)
            print(f"[DEBUG Groq] Success: {response.output_tokens} tokens", flush=True)
            
            return build_response(
                content=content,
                model=self.model,
                provider=self.name,
                usage={
                    "input": response.input_tokens,
                    "output": response.output_tokens,
                },
                tool_calls=tool_calls,
                finish_reason=response.finish_reason,
                raw=response.raw,
            )
            
        except llm_errors['LLMRateLimitError']:
            print(f"[ERROR Groq] Rate limit exceeded", flush=True)
            raise ProviderRateLimitError(self.name)
        except llm_errors['LLMAuthError']:
            print(f"[ERROR Groq] Authentication failed", flush=True)
            raise ProviderAuthError(self.name)
        except llm_errors['LLMError'] as e:
            error_str = str(e)
            print(f"[ERROR Groq] LLM error: {e}", flush=True)
            
            # Check if this is a tool validation error with failed_generation
            # Groq validates tool calls and rejects malformed ones, but gives us the text
            # Return it as content - agent.py will parse XML tool calls
            if "tool_use_failed" in error_str and "failed_generation" in error_str:
                try:
                    # Extract failed_generation from error
                    import ast
                    
                    dict_start = error_str.find("{'error':")
                    if dict_start == -1:
                        dict_start = error_str.find('{"error":')
                    
                    failed_text = ""
                    if dict_start != -1:
                        try:
                            error_dict = ast.literal_eval(error_str[dict_start:])
                            failed_text = error_dict.get('error', {}).get('failed_generation', '')
                        except:
                            match = re.search(r"'failed_generation':\s*'([^']+)'", error_str)
                            if match:
                                failed_text = match.group(1)
                    else:
                        match = re.search(r"'failed_generation':\s*'([^']+)'", error_str)
                        if match:
                            failed_text = match.group(1)
                    
                    if failed_text:
                        print(f"[DEBUG Groq] Returning failed_generation as content for agent to parse", flush=True)
                        # Return as content - agent.py's _parse_xml_tool_calls will handle it
                        return build_response(
                            content=failed_text,
                            model=self.model,
                            provider=self.name,
                            usage={"input": 0, "output": 0},
                            tool_calls=[],
                            finish_reason="stop",
                            raw=None,
                        )
                except Exception as parse_err:
                    print(f"[WARN Groq] Failed to extract failed_generation: {parse_err}", flush=True)
            
            raise ProviderError(self.name, f"LLM error: {e}")
    
    async def stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        system = kwargs.get("system")
        chat_messages = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                chat_messages.append(m)
        
        # Prepend system message if present
        if system:
            chat_messages = [{"role": "system", "content": system}] + chat_messages
        
        client = self._get_client()
        async for chunk in client.chat_stream(
            messages=chat_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk
    
    def count_tokens(self, messages: list[dict]) -> int:
        # Rough estimate
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        return MODEL_LIMITS.get(self.model, 32000)
