"""Simple Agent API - convenience wrapper for quick usage."""

from typing import Any, AsyncIterator, Union

from .definition import AgentDefinition, AgentTemplates
from .providers import get_provider, LLMProvider
from .memory import get_memory_strategy, MemoryStrategy
from .store import ThreadStore, MessageStore, AgentStore
from .context import DefaultContextBuilder
from .tools import get_tool_definitions, execute_tool_calls, register_tool, Tool
from .core import ProviderResponse, AgentError


class Agent:
    """
    Simple agent interface for quick usage.
    
    Example:
        # Minimal setup
        agent = Agent(
            name="Assistant",
            role="You help users with their questions.",
            provider="anthropic",
            api_key="sk-...",
        )
        response = await agent.chat("Hello!")
        
        # With definition
        agent = Agent(
            definition=AgentDefinition(
                role="You are a property manager assistant",
                goal="Help hosts manage vacation rentals",
                constraints=["Be concise", "Focus on actionable advice"],
            ),
            provider="openai",
            api_key="sk-...",
            model="gpt-4o",
        )
        
        # With tools
        agent = Agent(
            name="RAG Assistant",
            role="You help users find information in documents.",
            provider="anthropic",
            api_key="sk-...",
            tools=["search_documents", "ask_documents"],
        )
        
        # Continue conversation
        response1 = await agent.chat("What's the checkout time?")
        response2 = await agent.chat("And the check-in time?")  # Same thread
        
        # New thread
        agent.new_thread()
        response3 = await agent.chat("Different conversation")
    """
    
    def __init__(
        self,
        # Identity (one of these required)
        definition: AgentDefinition = None,
        role: str = None,
        name: str = "Assistant",
        
        # Provider config (required)
        provider: str = "anthropic",
        api_key: str = None,
        model: str = None,
        
        # Optional config
        goal: str = None,
        constraints: list[str] = None,
        personality: dict = None,
        tools: list[str] = None,
        memory_strategy: str = "last_n",
        memory_params: dict = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        
        # Database (optional - uses in-memory if not provided)
        conn: Any = None,
        auth: Any = None,
        
        # Injection guard (optional)
        judge_provider: LLMProvider = None,
    ):
        # Build definition
        if definition:
            self.definition = definition
        elif role:
            self.definition = AgentDefinition(
                role=role,
                goal=goal,
                constraints=constraints or [],
                personality=personality or {},
            )
        else:
            self.definition = AgentTemplates.assistant(name)
        
        self.name = name
        self.tools = tools or []
        self.temperature = temperature
        self.max_tokens = max_tokens
        
        # Setup provider
        provider_kwargs = {"model": model} if model else {}
        if api_key:
            provider_kwargs["api_key"] = api_key
        
        self._provider = get_provider(provider, **provider_kwargs)
        
        # Setup memory
        self._memory = get_memory_strategy(memory_strategy, **(memory_params or {"n": 20}))
        
        # Setup context builder
        self._context_builder = DefaultContextBuilder(self._memory)
        
        # In-memory storage if no DB provided
        self._use_memory_store = conn is None
        if self._use_memory_store:
            self._messages: list[dict] = []
            self._thread_id = "memory"
        else:
            self._conn = conn
            self._auth = auth
            self._threads = ThreadStore(conn)
            self._messages_store = MessageStore(conn)
            self._agents = AgentStore(conn)
            self._thread_id = None
            self._agent_id = None
        
        # Guardrails
        self._judge = judge_provider
    
    async def chat(self, content: str, user_id: str = "default") -> str:
        """
        Send a message and get a response.
        
        Args:
            content: User message
            user_id: User ID for auth (if using DB)
            
        Returns:
            Assistant response text
        """
        if self._use_memory_store:
            return await self._chat_memory(content)
        else:
            return await self._chat_db(content, user_id)
    
    async def _chat_memory(self, content: str) -> str:
        """Chat using in-memory storage."""
        # Add user message
        self._messages.append({"role": "user", "content": content})
        
        # Build context
        system_prompt = self.definition.compile()
        context = await self._context_builder.build(
            messages=self._messages,
            system_prompt=system_prompt,
        )
        
        # Get tools if any
        tools = get_tool_definitions(self.tools) if self.tools else None
        
        # Run completion loop
        response = await self._completion_loop(context, tools)
        
        # Save assistant message
        self._messages.append({"role": "assistant", "content": response.content})
        
        return response.content
    
    async def _chat_db(self, content: str, user_id: str) -> str:
        """Chat using database storage."""
        # Ensure agent and thread exist
        if not self._agent_id:
            await self._ensure_agent()
        if not self._thread_id:
            await self._ensure_thread(user_id)
        
        # Auth check if auth provided
        if self._auth:
            if not await self._auth.has_permission(user_id, "write", "thread", self._thread_id):
                raise PermissionError("Cannot write to this thread")
        
        # Save user message
        await self._messages_store.create(
            thread_id=self._thread_id,
            role="user",
            content=content,
            user_id=user_id,
        )
        
        # Build context
        system_prompt = self.definition.compile()
        messages = await self._messages_store.list(self._thread_id)
        context = await self._context_builder.build(
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            system_prompt=system_prompt,
        )
        
        # Get tools
        tools = get_tool_definitions(self.tools) if self.tools else None
        
        # Run completion loop
        response = await self._completion_loop(context, tools)
        
        # Save assistant message
        await self._messages_store.create(
            thread_id=self._thread_id,
            role="assistant",
            content=response.content,
        )
        
        return response.content
    
    async def _completion_loop(
        self,
        context: list[dict],
        tools: list[dict] = None,
        max_rounds: int = 10,
    ) -> ProviderResponse:
        """Run completion with tool loop."""
        messages = context.copy()
        
        for _ in range(max_rounds):
            response = await self._provider.run(
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                tools=tools,
            )
            
            if not response.tool_calls:
                return response
            
            # Add assistant message with tool calls
            messages.append({
                "role": "assistant",
                "content": response.content or "",
            })
            
            # Execute tools
            results = await execute_tool_calls(response.tool_calls)
            
            # Add tool results
            for result in results:
                messages.append({
                    "role": "tool",
                    "content": result.content,
                    "tool_call_id": result.tool_call_id,
                })
                
                # Also save to memory store
                if self._use_memory_store:
                    self._messages.append({
                        "role": "tool",
                        "content": result.content,
                    })
        
        return ProviderResponse(
            content="Max tool rounds reached.",
            usage={"input": 0, "output": 0},
            model=self._provider.model,
            provider=self._provider.name,
        )
    
    async def stream(self, content: str) -> AsyncIterator[str]:
        """
        Stream a response.
        
        Args:
            content: User message
            
        Yields:
            Response chunks
        """
        # Add user message
        if self._use_memory_store:
            self._messages.append({"role": "user", "content": content})
            messages = self._messages
        else:
            await self._messages_store.create(self._thread_id, "user", content)
            db_messages = await self._messages_store.list(self._thread_id)
            messages = [{"role": m["role"], "content": m["content"]} for m in db_messages]
        
        # Build context
        system_prompt = self.definition.compile()
        context = await self._context_builder.build(messages, system_prompt)
        
        # Stream
        full_response = []
        async for chunk in self._provider.stream(
            messages=context,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        ):
            full_response.append(chunk)
            yield chunk
        
        # Save response
        response_text = "".join(full_response)
        if self._use_memory_store:
            self._messages.append({"role": "assistant", "content": response_text})
        else:
            await self._messages_store.create(self._thread_id, "assistant", response_text)
    
    def new_thread(self):
        """Start a new conversation thread."""
        if self._use_memory_store:
            self._messages = []
        else:
            self._thread_id = None
    
    async def _ensure_agent(self):
        """Create agent in DB if not exists."""
        # Check if agent with same name exists
        existing = await self._agents.get_by_name(self.name)
        if existing:
            self._agent_id = existing["id"]
        else:
            agent = await self._agents.create(
                name=self.name,
                system_prompt=self.definition.compile(),
                model=self._provider.model,
                provider=self._provider.name,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                tools=self.tools,
                metadata={"definition": self.definition.to_dict()},
            )
            self._agent_id = agent["id"]
    
    async def _ensure_thread(self, user_id: str):
        """Create thread if not exists."""
        thread = await self._threads.create(agent_id=self._agent_id)
        self._thread_id = thread["id"]
        
        # Assign permission if auth provided
        if self._auth:
            await self._auth.assign_role(user_id, "owner", "thread", self._thread_id)
    
    @property
    def history(self) -> list[dict]:
        """Get conversation history."""
        if self._use_memory_store:
            return self._messages.copy()
        return []  # Use messages_store.list() for DB
    
    @property
    def system_prompt(self) -> str:
        """Get compiled system prompt."""
        return self.definition.compile()
    
    def __repr__(self) -> str:
        return f"Agent(name={self.name!r}, provider={self._provider.name!r}, model={self._provider.model!r})"


# Convenience function
def create_agent(
    role: str,
    provider: str = "anthropic",
    api_key: str = None,
    **kwargs,
) -> Agent:
    """
    Quick agent creation.
    
    Example:
        agent = create_agent(
            role="You help users with coding questions",
            provider="anthropic",
            api_key="sk-...",
        )
    """
    return Agent(role=role, provider=provider, api_key=api_key, **kwargs)
