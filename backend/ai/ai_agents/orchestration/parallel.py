"""
Parallel agent execution.

Run multiple agents concurrently and collect results.

Usage:
    from ai_agents.orchestration import ParallelAgents, parallel_chat
    
    # Option 1: ParallelAgents class
    parallel = ParallelAgents([agent1, agent2, agent3])
    results = await parallel.chat("Analyze this from your perspective")
    
    # Option 2: Simple function
    results = await parallel_chat(
        agents=[agent1, agent2, agent3],
        message="What do you think?",
    )
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional, Union
from dataclasses import dataclass, field


@dataclass
class AgentResult:
    """Result from a single agent in parallel execution."""
    agent_name: str
    content: str
    success: bool = True
    error: Optional[str] = None
    duration_ms: int = 0
    usage: dict = field(default_factory=dict)
    cost: float = 0.0
    
    def __str__(self) -> str:
        if self.success:
            return f"[{self.agent_name}]: {self.content}"
        return f"[{self.agent_name}] ERROR: {self.error}"


@dataclass
class ParallelResult:
    """Combined result from parallel agent execution."""
    results: list[AgentResult]
    total_duration_ms: int = 0
    total_cost: float = 0.0
    
    @property
    def successful(self) -> list[AgentResult]:
        """Get only successful results."""
        return [r for r in self.results if r.success]
    
    @property
    def failed(self) -> list[AgentResult]:
        """Get only failed results."""
        return [r for r in self.results if not r.success]
    
    @property
    def all_success(self) -> bool:
        """Check if all agents succeeded."""
        return all(r.success for r in self.results)
    
    @property
    def contents(self) -> list[str]:
        """Get all successful content strings."""
        return [r.content for r in self.successful]
    
    def by_agent(self, name: str) -> Optional[AgentResult]:
        """Get result by agent name."""
        for r in self.results:
            if r.agent_name == name:
                return r
        return None
    
    def to_context(self, format: str = "numbered") -> str:
        """
        Format results as context for another agent.
        
        Args:
            format: "numbered", "labeled", or "xml"
        """
        if format == "numbered":
            lines = []
            for i, r in enumerate(self.successful, 1):
                lines.append(f"{i}. [{r.agent_name}]: {r.content}")
            return "\n\n".join(lines)
        
        elif format == "labeled":
            lines = []
            for r in self.successful:
                lines.append(f"**{r.agent_name}**:\n{r.content}")
            return "\n\n---\n\n".join(lines)
        
        elif format == "xml":
            lines = []
            for r in self.successful:
                lines.append(f"<agent name=\"{r.agent_name}\">\n{r.content}\n</agent>")
            return "\n\n".join(lines)
        
        else:
            return "\n\n".join(self.contents)


class ParallelAgents:
    """
    Execute multiple agents in parallel.
    
    Example:
        researcher = Agent(role="Research analyst", ...)
        critic = Agent(role="Critical reviewer", ...)
        writer = Agent(role="Content writer", ...)
        
        parallel = ParallelAgents([researcher, critic, writer])
        
        # All agents process the same input concurrently
        results = await parallel.chat("Analyze the impact of AI on jobs")
        
        # Access individual results
        for result in results.successful:
            print(f"{result.agent_name}: {result.content[:100]}...")
        
        # Or format for synthesis
        context = results.to_context(format="xml")
    
    Warning:
        Each agent must be a SEPARATE INSTANCE. Using the same agent
        instance multiple times will cause state corruption.
        
        WRONG:  ParallelAgents([agent, agent, agent])
        RIGHT:  ParallelAgents([Agent(...), Agent(...), Agent(...)])
    """
    
    def __init__(
        self,
        agents: list,
        timeout: float = 120.0,
        fail_fast: bool = False,
        allow_duplicate_instances: bool = False,
    ):
        """
        Args:
            agents: List of Agent instances
            timeout: Max seconds to wait for all agents
            fail_fast: If True, cancel all on first failure
            allow_duplicate_instances: If True, skip duplicate check (unsafe)
        """
        # Validate no duplicate instances
        if not allow_duplicate_instances:
            instance_ids = [id(a) for a in agents]
            if len(instance_ids) != len(set(instance_ids)):
                duplicates = [
                    getattr(agents[i], 'name', f'Agent_{i}')
                    for i, aid in enumerate(instance_ids)
                    if instance_ids.count(aid) > 1
                ]
                raise ValueError(
                    f"ParallelAgents requires separate Agent instances. "
                    f"Same instance used multiple times will cause state corruption. "
                    f"Duplicates detected: {duplicates}. "
                    f"Create separate Agent instances for each slot."
                )
        
        self.agents = agents
        self.timeout = timeout
        self.fail_fast = fail_fast
    
    async def chat(
        self,
        message: str,
        user_id: str = "default",
        **kwargs,
    ) -> ParallelResult:
        """
        Send same message to all agents in parallel.
        
        Args:
            message: Message to send to all agents
            user_id: User ID for context
            **kwargs: Additional args passed to each agent.chat()
            
        Returns:
            ParallelResult with all agent responses
        """
        import time
        start_time = time.time()
        
        async def run_agent(agent) -> AgentResult:
            agent_start = time.time()
            try:
                # Get agent name
                name = getattr(agent, 'name', None) or getattr(agent, '_name', 'Agent')
                
                # Run chat
                response = await agent.chat(message, user_id=user_id, **kwargs)
                
                # Extract metadata if available
                usage = {}
                cost = 0.0
                if hasattr(agent, 'conversation_cost'):
                    cost = agent.conversation_cost
                if hasattr(agent, 'conversation_tokens'):
                    usage = {"total": agent.conversation_tokens}
                
                duration_ms = int((time.time() - agent_start) * 1000)
                
                return AgentResult(
                    agent_name=name,
                    content=response,
                    success=True,
                    duration_ms=duration_ms,
                    usage=usage,
                    cost=cost,
                )
                
            except Exception as e:
                name = getattr(agent, 'name', 'Agent')
                duration_ms = int((time.time() - agent_start) * 1000)
                return AgentResult(
                    agent_name=name,
                    content="",
                    success=False,
                    error=str(e),
                    duration_ms=duration_ms,
                )
        
        # Run all agents concurrently
        if self.fail_fast:
            # Use gather with return_exceptions=False to fail fast
            try:
                tasks = [run_agent(agent) for agent in self.agents]
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                results = [
                    AgentResult(
                        agent_name=getattr(a, 'name', 'Agent'),
                        content="",
                        success=False,
                        error="Timeout",
                    )
                    for a in self.agents
                ]
        else:
            # Use gather with return_exceptions=True to collect all
            tasks = [run_agent(agent) for agent in self.agents]
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self.timeout,
                )
                # Convert exceptions to error results
                final_results = []
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        name = getattr(self.agents[i], 'name', 'Agent')
                        final_results.append(AgentResult(
                            agent_name=name,
                            content="",
                            success=False,
                            error=str(result),
                        ))
                    else:
                        final_results.append(result)
                results = final_results
            except asyncio.TimeoutError:
                results = [
                    AgentResult(
                        agent_name=getattr(a, 'name', 'Agent'),
                        content="",
                        success=False,
                        error="Timeout",
                    )
                    for a in self.agents
                ]
        
        total_duration = int((time.time() - start_time) * 1000)
        total_cost = sum(r.cost for r in results)
        
        return ParallelResult(
            results=results,
            total_duration_ms=total_duration,
            total_cost=total_cost,
        )
    
    async def chat_different(
        self,
        messages: dict[str, str],
        user_id: str = "default",
        **kwargs,
    ) -> ParallelResult:
        """
        Send different messages to each agent.
        
        Args:
            messages: Dict mapping agent name to message
            user_id: User ID for context
            
        Returns:
            ParallelResult with all responses
        """
        import time
        start_time = time.time()
        
        async def run_agent(agent, message: str) -> AgentResult:
            agent_start = time.time()
            try:
                name = getattr(agent, 'name', 'Agent')
                response = await agent.chat(message, user_id=user_id, **kwargs)
                
                duration_ms = int((time.time() - agent_start) * 1000)
                cost = getattr(agent, 'conversation_cost', 0.0)
                
                return AgentResult(
                    agent_name=name,
                    content=response,
                    success=True,
                    duration_ms=duration_ms,
                    cost=cost,
                )
            except Exception as e:
                name = getattr(agent, 'name', 'Agent')
                return AgentResult(
                    agent_name=name,
                    content="",
                    success=False,
                    error=str(e),
                )
        
        # Match agents to messages
        tasks = []
        for agent in self.agents:
            name = getattr(agent, 'name', 'Agent')
            message = messages.get(name, messages.get('default', ''))
            if message:
                tasks.append(run_agent(agent, message))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle exceptions
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(AgentResult(
                    agent_name=f"Agent_{i}",
                    content="",
                    success=False,
                    error=str(result),
                ))
            else:
                final_results.append(result)
        
        total_duration = int((time.time() - start_time) * 1000)
        
        return ParallelResult(
            results=final_results,
            total_duration_ms=total_duration,
            total_cost=sum(r.cost for r in final_results),
        )


async def parallel_chat(
    agents: list,
    message: str,
    user_id: str = "default",
    timeout: float = 120.0,
    **kwargs,
) -> ParallelResult:
    """
    Convenience function for parallel agent execution.
    
    Args:
        agents: List of Agent instances
        message: Message to send to all
        user_id: User ID for context
        timeout: Max seconds to wait
        
    Returns:
        ParallelResult with all responses
        
    Example:
        results = await parallel_chat(
            agents=[researcher, analyst, writer],
            message="What are the key trends in AI?",
        )
        
        for r in results.successful:
            print(f"{r.agent_name}: {r.content}")
    """
    parallel = ParallelAgents(agents, timeout=timeout)
    return await parallel.chat(message, user_id=user_id, **kwargs)
