from __future__ import annotations
"""Agent runner - orchestrates everything."""

from typing import Any, AsyncIterator

# Backend imports (absolute - backend must be in sys.path)
try:
    from log import info, error
except ImportError:
    def info(msg, **kwargs): pass
    def error(msg, **kwargs): print(f"[ERROR] {msg}")

# Local imports
from ..core import ProviderResponse, AgentError, GuardrailError
from ..providers import LLMProvider, OpenAIAssistantProvider
from ..memory import MemoryStrategy, LastNMemory
from ..store import ThreadStore, MessageStore, AgentStore
from ..context import DefaultContextBuilder
from ..tools import get_tool_definitions, execute_tool_calls
from ..guardrails import InjectionGuardrail


class AgentRunner:
    """
    Orchestrates agent execution.
    
    Handles:
    - Auth checks
    - Message persistence
    - Context building
    - LLM calls
    - Tool execution
    - Guardrails (LLM-as-judge)
    
    Auto-switches to OpenAI Assistants API when:
    - Provider is OpenAI
    - Memory strategy is last_n
    """
    
    def __init__(
        self,
        conn: Any,
        auth: Any,
        provider: LLMProvider,
        memory: MemoryStrategy,
        judge_provider: LLMProvider = None,
        max_tool_rounds: int = 10,
    ):
        """
        Args:
            conn: Database connection
            auth: Auth service
            provider: Main LLM provider
            memory: Memory strategy
            judge_provider: Fast LLM for injection detection (e.g., gpt-4o-mini)
            max_tool_rounds: Max tool execution rounds
        """
        self.conn = conn
        self.auth = auth
        self.memory = memory
        self.max_tool_rounds = max_tool_rounds
        
        # Auto-switch to OpenAI Assistants when OpenAI + last_n
        self.provider = self._maybe_upgrade_to_assistant(provider, memory)
        self._use_assistant_api = isinstance(self.provider, OpenAIAssistantProvider)
        
        # Stores
        self.threads = ThreadStore(conn)
        self.messages = MessageStore(conn)
        self.agents = AgentStore(conn)
        
        # Context builder (not used for Assistant API)
        self.context_builder = DefaultContextBuilder(memory)
        
        # Guardrails (LLM-based)
        self.injection_guard = InjectionGuardrail(judge_provider) if judge_provider else None
    
    def _maybe_upgrade_to_assistant(
        self,
        provider: LLMProvider,
        memory: MemoryStrategy,
    ) -> LLMProvider:
        """Switch to OpenAI Assistants API if OpenAI + last_n."""
        if provider.name == "openai" and isinstance(memory, LastNMemory):
            info("Auto-switching to OpenAI Assistants API (openai + last_n)")
            return OpenAIAssistantProvider(
                api_key=provider.client.api_key,
                model=provider.model,
            )
        return provider
    
    async def run(
        self,
        thread_id: str,
        user_id: str,
        content: str,
        attachments: list[str] = None,
    ) -> dict:
        """
        Process a user message and get assistant response.
        
        Args:
            thread_id: Thread ID
            user_id: User ID (for auth)
            content: Message content
            attachments: Optional attachment paths
            
        Returns:
            Assistant message dict
        """
        info("AgentRunner.run", thread_id=thread_id, user_id=user_id)
        
        # Auth check
        if not await self.auth.has_permission(user_id, "write", "thread", thread_id):
            raise PermissionError("Cannot write to this thread")
        
        # Guardrails on input (async LLM call)
        if self.injection_guard:
            await self.injection_guard.check(content)
        
        # Get thread + agent
        thread = await self.threads.get(thread_id)
        if not thread:
            raise AgentError(f"Thread not found: {thread_id}")
        
        agent = await self.agents.get(thread["agent_id"])
        if not agent:
            raise AgentError(f"Agent not found: {thread['agent_id']}")
        
        # Setup OpenAI Assistant if needed
        if self._use_assistant_api:
            await self._setup_assistant(agent, thread)
        
        # Save user message
        await self.messages.create(
            thread_id=thread_id,
            role="user",
            content=content,
            user_id=user_id,
            attachments=attachments,
        )
        
        # Get tool definitions
        tools = get_tool_definitions(agent.get("tools", [])) if agent.get("tools") else None
        
        # Run completion loop (handles tool calls)
        response = await self._completion_loop(thread_id, agent, tools)
        
        # Save assistant message
        assistant_msg = await self.messages.create(
            thread_id=thread_id,
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        )
        
        info("AgentRunner.run complete", message_id=assistant_msg["id"])
        return assistant_msg
    
    async def _setup_assistant(self, agent: dict, thread: dict):
        """Setup OpenAI Assistant on first call."""
        provider = self.provider  # OpenAIAssistantProvider
        
        # Get or create assistant
        if not provider.assistant_id:
            # Check if agent has stored assistant_id
            stored_id = agent.get("metadata", {}).get("openai_assistant_id")
            if stored_id:
                provider.assistant_id = stored_id
            else:
                # Create new assistant
                tools = get_tool_definitions(agent.get("tools", [])) if agent.get("tools") else None
                assistant_id = await provider.get_or_create_assistant(
                    name=agent["name"],
                    instructions=agent.get("system_prompt", ""),
                    tools=tools,
                )
                # Store for future use
                await self.agents.update(
                    agent["id"],
                    metadata={**agent.get("metadata", {}), "openai_assistant_id": assistant_id}
                )
        
        # Load thread mapping if exists
        openai_thread_id = thread.get("metadata", {}).get("openai_thread_id")
        if openai_thread_id:
            provider.set_thread_mapping(thread["id"], openai_thread_id)
        else:
            # Will be created on first call, then we store it
            pass
    
    async def _completion_loop(
        self,
        thread_id: str,
        agent: dict,
        tools: list[dict] = None,
    ) -> ProviderResponse:
        """Run completion with tool loop."""
        
        for round_num in range(self.max_tool_rounds):
            # Build context (skipped for Assistant API - it manages its own)
            messages = await self.messages.list(thread_id)
            
            if self._use_assistant_api:
                # Assistant API: just pass latest message, it handles history
                context = [{"role": m["role"], "content": m["content"]} for m in messages]
            else:
                # Standard API: build context with memory strategy
                context = await self.context_builder.build(
                    messages=[{"role": m["role"], "content": m["content"]} for m in messages],
                    system_prompt=agent.get("system_prompt"),
                    tools=tools,
                )
            
            # Call LLM
            response = await self.provider.run(
                messages=context,
                temperature=agent.get("temperature", 0.7),
                max_tokens=agent.get("max_tokens", 4096),
                tools=tools,
                thread_id=thread_id,
            )
            
            # Save OpenAI thread mapping after first call
            if self._use_assistant_api and round_num == 0:
                await self._save_thread_mapping(thread_id)
            
            # If no tool calls, we're done
            if not response.tool_calls:
                return response
            
            # Execute tools
            info("Executing tools", count=len(response.tool_calls), round=round_num)
            
            # Save assistant message with tool calls
            await self.messages.create(
                thread_id=thread_id,
                role="assistant",
                content=response.content or "",
                tool_calls=response.tool_calls,
            )
            
            # Execute tool calls
            results = await execute_tool_calls(response.tool_calls)
            
            # Save tool results
            for result in results:
                await self.messages.create(
                    thread_id=thread_id,
                    role="tool",
                    content=result.content,
                    tool_call_id=result.tool_call_id,
                )
            
            # For Assistant API, submit tool outputs instead of looping
            if self._use_assistant_api:
                run_id = response.tool_calls[0].get("_run_id")
                if run_id:
                    response = await self.provider.submit_tool_outputs(
                        thread_id=thread_id,
                        run_id=run_id,
                        tool_outputs=[
                            {"tool_call_id": r.tool_call_id, "output": r.content}
                            for r in results
                        ],
                    )
                    if not response.tool_calls:
                        return response
                    # Continue loop if more tool calls
        
        # Max rounds reached
        error("Max tool rounds reached", thread_id=thread_id)
        return ProviderResponse(
            content="I apologize, but I wasn't able to complete the task within the allowed number of steps.",
            usage={"input": 0, "output": 0},
            model=self.provider.model,
            provider=self.provider.name,
        )
    
    async def _save_thread_mapping(self, thread_id: str):
        """Save OpenAI thread ID to our thread metadata."""
        if not self._use_assistant_api:
            return
        
        openai_thread_id = self.provider._thread_cache.get(thread_id)
        if openai_thread_id:
            thread = await self.threads.get(thread_id)
            if thread:
                metadata = thread.get("metadata", {})
                if metadata.get("openai_thread_id") != openai_thread_id:
                    await self.threads.update(
                        thread_id,
                        metadata={**metadata, "openai_thread_id": openai_thread_id}
                    )
    
    async def stream(
        self,
        thread_id: str,
        user_id: str,
        content: str,
        attachments: list[str] = None,
    ) -> AsyncIterator[str]:
        """
        Stream a response.
        
        Note: Streaming doesn't support tool loops.
        Use run() for tool-using agents.
        """
        # Auth check
        if not await self.auth.has_permission(user_id, "write", "thread", thread_id):
            raise PermissionError("Cannot write to this thread")
        
        # Guardrails
        if self.injection_guard:
            await self.injection_guard.check(content)
        
        # Get thread + agent
        thread = await self.threads.get(thread_id)
        agent = await self.agents.get(thread["agent_id"])
        
        # Save user message
        await self.messages.create(
            thread_id=thread_id,
            role="user",
            content=content,
            user_id=user_id,
            attachments=attachments,
        )
        
        # Build context
        messages = await self.messages.list(thread_id)
        context = await self.context_builder.build(
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            system_prompt=agent.get("system_prompt"),
        )
        
        # Stream response
        full_response = []
        async for chunk in self.provider.stream(
            messages=context,
            temperature=agent.get("temperature", 0.7),
            max_tokens=agent.get("max_tokens", 4096),
            thread_id=thread_id,
        ):
            full_response.append(chunk)
            yield chunk
        
        # Save complete response
        await self.messages.create(
            thread_id=thread_id,
            role="assistant",
            content="".join(full_response),
        )
