"""Groq provider - fast inference with OpenAI-compatible API."""
from __future__ import annotations

from typing import AsyncIterator
import json
import re
import openai

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


def _parse_xml_tool_calls(content: str) -> tuple[str, list[dict]]:
    """
    Parse XML-style tool calls that Llama sometimes outputs.
    
    Patterns:
    - <function(name)>{json}</function>
    - <function(name) "{escaped_json}"</function>  (with space and quoted)
    - <function=name>{json}</function>
    - <function=name{json}</function>  (no separator - Groq's format)
    
    Returns: (cleaned_content, tool_calls)
    """
    tool_calls = []
    cleaned = content
    matched_spans = set()  # Track (start, end) of already matched regions
    
    print(f"[DEBUG _parse_xml_tool_calls] Input: {content[:200]}")
    
    # Try multiple patterns - ORDER MATTERS (most specific first, unclosed last)
    patterns = [
        # MOST FLEXIBLE: <function=name SPACE {json with possible nesting} SPACE </function>
        # Uses greedy match for JSON content, then trims
        (r'<function=(\w+)\s+(\{.*\})\s*</function>', "flex_greedy"),
        # NEW: <function=name({json})</function> - parentheses around args (Llama variant)
        (r'<function=(\w+)\((\{.+?\})\)</function>', "eq_paren_args"),
        # NEW: <function(name)={json}</function> - parentheses around NAME with = (Groq/Llama variant)
        (r'<function\((\w+)\)=\s*(\{.+?\})\s*</function>', "paren_eq"),
        # Standard formats with separator
        (r'<function\((\w+)\)>\s*(\{.+?\})\s*</function>', "paren_gt"),
        (r'<function\((\w+)\)\s*(\{.+?\})\s*</function>', "paren"),
        (r'<function=(\w+)>\s*(\{.+?\})\s*</function>', "eq_gt"),
        # NO separator between name and JSON (Groq's format): <function=name{json}</function>
        (r'<function=(\w+)(\{.+?\})</function>', "eq_no_space"),
        # SPACE separator between name and JSON: <function=name {json}</function>
        (r'<function=(\w+)\s+(\{.+?\})\s*</function>', "eq_space"),
        # SPACE separator with stray > before closing: <function=name {json}></function>
        (r'<function=(\w+)\s+(\{.+?\})\s*>\s*</function>', "eq_space_gt"),
        # Quoted escaped format: <function(name) "escaped_json"</function>
        (r'<function\((\w+)\)\s*"(.+?)"\s*</function>', "paren_quoted"),
        
        # UNCLOSED TAGS (common in Groq error recovery - failed_generation often truncated)
        # <function=name>{json} (no closing tag, with >)
        (r'<function=(\w+)>(\{.+?\})\s*$', "unclosed_gt"),
        # <function=name>{json} (no closing tag, no >, end of string)
        (r'<function=(\w+)(\{.+\})\s*$', "unclosed_no_gt"),
        # <function=name {json} (space separator, no closing tag)
        (r'<function=(\w+)\s+(\{.+\})\s*$', "unclosed_space"),
        # <function(name)={json} (no closing tag - paren_eq variant)
        (r'<function\((\w+)\)=\s*(\{.+\})\s*$', "unclosed_paren_eq"),
    ]
    
    for pattern, pattern_name in patterns:
        for match in re.finditer(pattern, content, re.DOTALL):
            # Check if this span overlaps with any already-matched span
            span = (match.start(), match.end())
            overlaps = any(
                not (span[1] <= existing[0] or span[0] >= existing[1])
                for existing in matched_spans
            )
            if overlaps:
                continue  # Skip - already matched by a previous pattern
            
            print(f"[DEBUG _parse_xml_tool_calls] Pattern '{pattern_name}' matched!")
            name = match.group(1)
            json_part = match.group(2).strip()
            
            try:
                # If it looks like escaped JSON (has \"), unescape it
                if '\\"' in json_part or '\\n' in json_part:
                    # Unescape the JSON string
                    json_part = json_part.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
                
                # Find balanced JSON
                json_start = json_part.find('{')
                if json_start == -1:
                    continue
                
                # Balance braces to find end
                depth = 0
                json_end = json_start
                for i, c in enumerate(json_part[json_start:], json_start):
                    if c == '{':
                        depth += 1
                    elif c == '}':
                        depth -= 1
                        if depth == 0:
                            json_end = i + 1
                            break
                
                args_str = json_part[json_start:json_end]
                args = json.loads(args_str)
                
                tool_calls.append({
                    "id": f"xml_{name}_{len(tool_calls)}",
                    "name": name,
                    "arguments": args,
                })
                
                # Mark this span as matched
                matched_spans.add(span)
                
                # Remove this match from content
                cleaned = cleaned.replace(match.group(0), '', 1)
                print(f"[DEBUG Groq] Parsed XML tool call: {name}, args={args}")
                
            except (json.JSONDecodeError, IndexError) as e:
                print(f"[WARN Groq] Failed to parse XML tool call: {e}, json_part: {json_part[:100]}")
    
    if tool_calls:
        cleaned = cleaned.strip()
        print(f"[DEBUG Groq] Parsed {len(tool_calls)} XML tool calls from content")
    
    return cleaned, tool_calls


class GroqProvider(LLMProvider):
    """Groq provider - extremely fast inference."""
    
    name = "groq"
    
    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile", **kwargs):
        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.groq.com/openai/v1",
        )
        self.model = model
    
    @circuit_breaker(name="groq")
    @with_timeout(seconds=60)
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
        
        try:
            params = {
                "model": self.model,
                "messages": chat_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            
            # Add system message if present
            if system:
                params["messages"] = [{"role": "system", "content": system}] + chat_messages
            
            # Add tools if provided
            if tools:
                params["tools"] = [
                    {"type": "function", "function": t} for t in tools
                ]
                params["tool_choice"] = "auto"
                print(f"[DEBUG Groq] Sending {len(tools)} tools", flush=True)
            
            response = await self.client.chat.completions.create(**params)
            
            choice = response.choices[0]
            content = choice.message.content or ""
            
            # Extract tool calls from API response
            tool_calls = parse_openai_tool_calls(choice.message.tool_calls)
            if tool_calls:
                print(f"[DEBUG Groq] Got {len(tool_calls)} tool calls from API", flush=True)
            
            # Note: XML-style tool calls in content are now handled at the Agent level
            # in agent.py _completion_loop, so we don't parse them here
            
            info("Groq response",
                 input_tokens=response.usage.prompt_tokens,
                 output_tokens=response.usage.completion_tokens)
            print(f"[DEBUG Groq] Success: {response.usage.completion_tokens} tokens", flush=True)
            
            return build_response(
                content=content,
                model=self.model,
                provider=self.name,
                usage={
                    "input": response.usage.prompt_tokens,
                    "output": response.usage.completion_tokens,
                },
                tool_calls=tool_calls,
                finish_reason=choice.finish_reason,
                raw=response,
            )
            
        except openai.RateLimitError:
            print(f"[ERROR Groq] Rate limit exceeded", flush=True)
            raise ProviderRateLimitError(self.name)
        except openai.AuthenticationError:
            print(f"[ERROR Groq] Authentication failed", flush=True)
            raise ProviderAuthError(self.name)
        except openai.BadRequestError as e:
            error_str = str(e)
            print(f"[ERROR Groq] Bad request: {e}", flush=True)
            
            # Check if this is a tool validation error with failed_generation
            # Groq validates tool calls and rejects malformed ones, but gives us the text
            if "tool_use_failed" in error_str and "failed_generation" in error_str:
                # Extract the failed generation from error
                try:
                    # The error contains the raw XML tool call - extract it
                    # Format: 'failed_generation': '<function=name{json}</function>'
                    import ast
                    
                    # Try to find the dict in the error string
                    dict_start = error_str.find("{'error':")
                    if dict_start == -1:
                        dict_start = error_str.find('{"error":')
                    
                    if dict_start != -1:
                        # Extract the dict portion
                        dict_str = error_str[dict_start:]
                        # Parse it
                        try:
                            error_dict = ast.literal_eval(dict_str)
                            failed_text = error_dict.get('error', {}).get('failed_generation', '')
                        except:
                            # Fallback: regex extract
                            match = re.search(r"'failed_generation':\s*'([^']+)'", error_str)
                            if match:
                                failed_text = match.group(1)
                            else:
                                failed_text = ""
                    else:
                        # Fallback: regex extract
                        match = re.search(r"'failed_generation':\s*'([^']+)'", error_str)
                        if match:
                            failed_text = match.group(1)
                        else:
                            failed_text = ""
                    
                    if failed_text:
                        print(f"[DEBUG Groq] Recovering from failed tool call, text: {failed_text[:200]}", flush=True)
                        
                        # Try to parse XML tool calls from the failed text
                        cleaned, xml_tool_calls = _parse_xml_tool_calls(failed_text)
                        
                        # If we found tool calls, try to fix common issues
                        fixed_tool_calls = []
                        for tc in xml_tool_calls:
                            args = tc.get("arguments", {})
                            # If 'updates' is missing, wrap the non-reason args in 'updates'
                            if "updates" not in args and tc.get("name") == "update_context":
                                reason = args.pop("reason", "Auto-saved")
                                fixed_args = {"updates": args, "reason": reason}
                                tc["arguments"] = fixed_args
                                print(f"[DEBUG Groq] Fixed update_context args: {fixed_args}", flush=True)
                            fixed_tool_calls.append(tc)
                        
                        if fixed_tool_calls:
                            print(f"[DEBUG Groq] Recovered {len(fixed_tool_calls)} tool calls from error", flush=True)
                            # Return the recovered response
                            return ProviderResponse(
                                content=cleaned,
                                usage={"input": 0, "output": 0},  # Unknown
                                model=self.model,
                                provider=self.name,
                                tool_calls=fixed_tool_calls,
                                finish_reason="tool_calls",
                                raw=None,
                            )
                except Exception as parse_err:
                    print(f"[WARN Groq] Failed to recover tool call: {parse_err}", flush=True)
            
            raise ProviderError(self.name, f"Bad request: {e}")
        except openai.APIError as e:
            error("Groq API error", error=str(e))
            print(f"[ERROR Groq] API error: {e}", flush=True)
            raise ProviderError(self.name, str(e))
    
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
        
        params = {
            "model": self.model,
            "messages": chat_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        
        if system:
            params["messages"] = [{"role": "system", "content": system}] + chat_messages
        
        stream = await self.client.chat.completions.create(**params)
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    
    def count_tokens(self, messages: list[dict]) -> int:
        # Rough estimate
        total_chars = sum(len(m.get("content", "")) for m in messages)
        return total_chars // 4
    
    @property
    def max_context_tokens(self) -> int:
        return MODEL_LIMITS.get(self.model, 32000)
