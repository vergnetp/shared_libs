"""
Supervisor pattern for multi-agent coordination.

A supervisor agent breaks down tasks, delegates to workers, and synthesizes results.

Usage:
    from ai_agents import Agent
    from ai_agents.orchestration import Supervisor
    
    # Create specialized workers
    researcher = Agent(role="Research analyst", name="Researcher", ...)
    writer = Agent(role="Content writer", name="Writer", ...)
    critic = Agent(role="Critical reviewer", name="Critic", ...)
    
    # Create supervisor
    supervisor = Supervisor(
        planner=Agent(role="Task planner", ...),
        workers={"Researcher": researcher, "Writer": writer, "Critic": critic},
        synthesizer=Agent(role="Synthesize results", ...),
    )
    
    # Run complex task
    result = await supervisor.run("Write a blog post about AI trends")
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional, Callable, Union
from dataclasses import dataclass, field
from enum import Enum


class WorkerSelectionMode(str, Enum):
    """How supervisor selects workers."""
    ALL = "all"           # Run all workers in parallel
    SELECTIVE = "selective"  # Planner chooses which workers
    SEQUENTIAL = "sequential"  # Run workers in sequence


@dataclass
class SupervisorConfig:
    """Configuration for supervisor behavior."""
    
    # Worker selection
    mode: WorkerSelectionMode = WorkerSelectionMode.SELECTIVE
    max_workers_per_step: int = 5
    
    # Iteration control
    max_iterations: int = 3
    allow_replanning: bool = True
    
    # Timeouts
    planning_timeout: float = 60.0
    worker_timeout: float = 120.0
    synthesis_timeout: float = 60.0
    
    # Quality control
    require_all_workers: bool = False  # Fail if any worker fails
    min_successful_workers: int = 1
    
    # Output
    include_reasoning: bool = True
    include_worker_outputs: bool = False


@dataclass 
class WorkerTask:
    """Task assigned to a worker by the planner."""
    worker_name: str
    task: str
    context: str = ""
    priority: int = 1


@dataclass
class WorkerResult:
    """Result from a worker execution."""
    worker_name: str
    task: str
    output: str
    success: bool = True
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class SupervisorResult:
    """Final result from supervisor orchestration."""
    content: str
    success: bool = True
    
    # Execution details
    plan: list[WorkerTask] = field(default_factory=list)
    worker_results: list[WorkerResult] = field(default_factory=list)
    iterations: int = 1
    
    # Metadata
    total_duration_ms: int = 0
    total_cost: float = 0.0
    reasoning: str = ""
    
    @property
    def successful_workers(self) -> list[WorkerResult]:
        return [r for r in self.worker_results if r.success]
    
    @property
    def failed_workers(self) -> list[WorkerResult]:
        return [r for r in self.worker_results if not r.success]


# Default prompts
PLANNER_SYSTEM_PROMPT = """You are a task planner that breaks down complex tasks and assigns them to specialized workers.

Available workers:
{workers}

For each task, output a JSON array of assignments:
```json
[
  {{"worker": "WorkerName", "task": "Specific task description", "context": "Any relevant context"}}
]
```

Rules:
- Only assign to available workers listed above
- Be specific about what each worker should do
- Include relevant context from the original request
- Order tasks by dependency (independent tasks first)
- If the task is simple, assign to just one worker
"""

SYNTHESIZER_SYSTEM_PROMPT = """You are a synthesizer that combines outputs from multiple specialists into a coherent response.

Your job:
1. Review all worker outputs
2. Identify key insights from each
3. Resolve any contradictions
4. Produce a unified, high-quality response

Do NOT just concatenate outputs - synthesize them into something better than any individual part.
"""


class Supervisor:
    """
    Coordinates multiple worker agents using a plan-execute-synthesize pattern.
    
    Flow:
    1. Planner analyzes task and creates work assignments
    2. Workers execute their assigned tasks (parallel or sequential)
    3. Synthesizer combines worker outputs into final response
    4. (Optional) Iterate if results need improvement
    
    Example:
        # Simple setup
        supervisor = Supervisor(
            workers={
                "Researcher": research_agent,
                "Writer": writer_agent,
            }
        )
        result = await supervisor.run("Write about quantum computing")
        
        # Full control
        supervisor = Supervisor(
            planner=custom_planner_agent,
            workers={"A": agent_a, "B": agent_b, "C": agent_c},
            synthesizer=custom_synthesizer_agent,
            config=SupervisorConfig(
                mode=WorkerSelectionMode.ALL,
                max_iterations=2,
            ),
        )
    """
    
    def __init__(
        self,
        workers: dict[str, Any],
        planner: Any = None,
        synthesizer: Any = None,
        config: SupervisorConfig = None,
    ):
        """
        Args:
            workers: Dict mapping worker names to Agent instances
            planner: Agent for task planning (auto-created if None)
            synthesizer: Agent for combining results (auto-created if None)
            config: Supervisor configuration
        """
        self.workers = workers
        self.config = config or SupervisorConfig()
        
        # Store for lazy initialization
        self._planner = planner
        self._synthesizer = synthesizer
        self._planner_initialized = planner is not None
        self._synthesizer_initialized = synthesizer is not None
    
    def _get_worker_descriptions(self) -> str:
        """Get formatted worker descriptions for planner prompt."""
        lines = []
        for name, agent in self.workers.items():
            role = getattr(agent, 'role', None)
            if not role:
                definition = getattr(agent, 'definition', None)
                if definition:
                    role = getattr(definition, 'role', 'General assistant')
                else:
                    role = 'General assistant'
            lines.append(f"- {name}: {role}")
        return "\n".join(lines)
    
    async def _ensure_planner(self):
        """Lazily initialize planner agent."""
        if self._planner_initialized:
            return
        
        from ..agent import Agent
        
        worker_desc = self._get_worker_descriptions()
        system_prompt = PLANNER_SYSTEM_PROMPT.format(workers=worker_desc)
        
        # Get provider config from first worker
        first_worker = next(iter(self.workers.values()))
        provider = getattr(first_worker, '_provider', None)
        
        self._planner = Agent(
            role=system_prompt,
            name="Planner",
            _provider=provider,
            temperature=0.3,  # More deterministic planning
        )
        self._planner_initialized = True
    
    async def _ensure_synthesizer(self):
        """Lazily initialize synthesizer agent."""
        if self._synthesizer_initialized:
            return
        
        from ..agent import Agent
        
        first_worker = next(iter(self.workers.values()))
        provider = getattr(first_worker, '_provider', None)
        
        self._synthesizer = Agent(
            role=SYNTHESIZER_SYSTEM_PROMPT,
            name="Synthesizer",
            _provider=provider,
            temperature=0.5,
        )
        self._synthesizer_initialized = True
    
    async def run(
        self,
        task: str,
        user_id: str = "default",
        context: str = "",
    ) -> SupervisorResult:
        """
        Execute a complex task using worker agents.
        
        Args:
            task: The task to accomplish
            user_id: User ID for context
            context: Additional context for planning
            
        Returns:
            SupervisorResult with final output and execution details
        """
        import time
        start_time = time.time()
        
        all_worker_results = []
        all_plans = []
        total_cost = 0.0
        reasoning_parts = []
        
        # Initialize agents if needed
        await self._ensure_planner()
        await self._ensure_synthesizer()
        
        current_task = task
        current_context = context
        
        for iteration in range(self.config.max_iterations):
            # Step 1: Plan
            plan = await self._plan(current_task, current_context)
            all_plans.extend(plan)
            
            if not plan:
                reasoning_parts.append(f"Iteration {iteration + 1}: No tasks planned")
                break
            
            reasoning_parts.append(
                f"Iteration {iteration + 1}: Planned {len(plan)} tasks - " +
                ", ".join(f"{t.worker_name}" for t in plan)
            )
            
            # Step 2: Execute workers
            worker_results = await self._execute_workers(plan, user_id)
            all_worker_results.extend(worker_results)
            
            # Track costs
            for wr in worker_results:
                worker = self.workers.get(wr.worker_name)
                if worker and hasattr(worker, 'conversation_cost'):
                    total_cost += worker.conversation_cost
            
            # Check if we have enough successful results
            successful = [r for r in worker_results if r.success]
            if len(successful) < self.config.min_successful_workers:
                if self.config.require_all_workers:
                    return SupervisorResult(
                        content="",
                        success=False,
                        plan=all_plans,
                        worker_results=all_worker_results,
                        iterations=iteration + 1,
                        total_duration_ms=int((time.time() - start_time) * 1000),
                        total_cost=total_cost,
                        reasoning="\n".join(reasoning_parts) + "\nFailed: Not enough successful workers",
                    )
            
            # Step 3: Synthesize
            synthesis_input = self._format_for_synthesis(task, worker_results)
            final_output = await self._synthesize(synthesis_input)
            
            # Check if we need another iteration
            if not self.config.allow_replanning or iteration == self.config.max_iterations - 1:
                break
            
            # Could add quality check here to decide if replanning needed
            # For now, just do one iteration unless explicitly configured
            break
        
        total_duration = int((time.time() - start_time) * 1000)
        
        return SupervisorResult(
            content=final_output,
            success=True,
            plan=all_plans,
            worker_results=all_worker_results,
            iterations=iteration + 1,
            total_duration_ms=total_duration,
            total_cost=total_cost,
            reasoning="\n".join(reasoning_parts),
        )
    
    async def _plan(self, task: str, context: str = "") -> list[WorkerTask]:
        """Have planner create work assignments."""
        
        if self.config.mode == WorkerSelectionMode.ALL:
            # Skip planning, assign task to all workers
            return [
                WorkerTask(worker_name=name, task=task, context=context)
                for name in self.workers.keys()
            ]
        
        # Ask planner to create assignments
        prompt = f"Task: {task}"
        if context:
            prompt += f"\n\nContext: {context}"
        
        try:
            response = await asyncio.wait_for(
                self._planner.chat(prompt),
                timeout=self.config.planning_timeout,
            )
            
            # Parse JSON from response
            assignments = self._parse_plan(response)
            
            # Filter to valid workers and limit count
            valid_assignments = []
            for a in assignments[:self.config.max_workers_per_step]:
                if a.worker_name in self.workers:
                    valid_assignments.append(a)
            
            return valid_assignments
            
        except asyncio.TimeoutError:
            # Fallback: assign to all workers
            return [
                WorkerTask(worker_name=name, task=task, context=context)
                for name in list(self.workers.keys())[:self.config.max_workers_per_step]
            ]
        except Exception as e:
            print(f"[Supervisor] Planning failed: {e}")
            return []
    
    def _parse_plan(self, response: str) -> list[WorkerTask]:
        """Parse planner response into WorkerTask list."""
        # Try to find JSON in response
        json_match = re.search(r'\[[\s\S]*\]', response)
        if not json_match:
            return []
        
        try:
            data = json.loads(json_match.group())
            tasks = []
            for item in data:
                if isinstance(item, dict) and 'worker' in item:
                    tasks.append(WorkerTask(
                        worker_name=item['worker'],
                        task=item.get('task', ''),
                        context=item.get('context', ''),
                        priority=item.get('priority', 1),
                    ))
            return tasks
        except json.JSONDecodeError:
            return []
    
    async def _execute_workers(
        self,
        plan: list[WorkerTask],
        user_id: str,
    ) -> list[WorkerResult]:
        """Execute worker tasks (parallel or sequential)."""
        
        if self.config.mode == WorkerSelectionMode.SEQUENTIAL:
            # Sequential execution
            results = []
            for task in plan:
                result = await self._execute_single_worker(task, user_id)
                results.append(result)
            return results
        
        else:
            # Parallel execution
            tasks = [
                self._execute_single_worker(task, user_id)
                for task in plan
            ]
            
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=self.config.worker_timeout,
                )
                
                # Convert exceptions to error results
                final_results = []
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        final_results.append(WorkerResult(
                            worker_name=plan[i].worker_name,
                            task=plan[i].task,
                            output="",
                            success=False,
                            error=str(result),
                        ))
                    else:
                        final_results.append(result)
                
                return final_results
                
            except asyncio.TimeoutError:
                return [
                    WorkerResult(
                        worker_name=t.worker_name,
                        task=t.task,
                        output="",
                        success=False,
                        error="Timeout",
                    )
                    for t in plan
                ]
    
    async def _execute_single_worker(
        self,
        task: WorkerTask,
        user_id: str,
    ) -> WorkerResult:
        """Execute a single worker task."""
        import time
        start_time = time.time()
        
        worker = self.workers.get(task.worker_name)
        if not worker:
            return WorkerResult(
                worker_name=task.worker_name,
                task=task.task,
                output="",
                success=False,
                error=f"Worker not found: {task.worker_name}",
            )
        
        try:
            # Build prompt with task and context
            prompt = task.task
            if task.context:
                prompt = f"{task.context}\n\nTask: {task.task}"
            
            response = await worker.chat(prompt, user_id=user_id)
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            return WorkerResult(
                worker_name=task.worker_name,
                task=task.task,
                output=response,
                success=True,
                duration_ms=duration_ms,
            )
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            return WorkerResult(
                worker_name=task.worker_name,
                task=task.task,
                output="",
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )
    
    def _format_for_synthesis(
        self,
        original_task: str,
        results: list[WorkerResult],
    ) -> str:
        """Format worker results for synthesizer."""
        parts = [f"Original task: {original_task}", "", "Worker outputs:"]
        
        for r in results:
            if r.success:
                parts.append(f"\n### {r.worker_name}")
                parts.append(r.output)
            else:
                parts.append(f"\n### {r.worker_name} (FAILED)")
                parts.append(f"Error: {r.error}")
        
        return "\n".join(parts)
    
    async def _synthesize(self, input_text: str) -> str:
        """Have synthesizer combine worker outputs."""
        try:
            response = await asyncio.wait_for(
                self._synthesizer.chat(input_text),
                timeout=self.config.synthesis_timeout,
            )
            return response
        except asyncio.TimeoutError:
            return "Synthesis timed out. Raw worker outputs were collected."
        except Exception as e:
            return f"Synthesis failed: {e}"
    
    # Convenience methods
    
    async def research(self, topic: str, **kwargs) -> SupervisorResult:
        """Shortcut for research-style tasks."""
        return await self.run(
            task=f"Research and provide comprehensive information about: {topic}",
            **kwargs,
        )
    
    async def analyze(self, content: str, **kwargs) -> SupervisorResult:
        """Shortcut for analysis tasks."""
        return await self.run(
            task=f"Analyze the following content from multiple perspectives:\n\n{content}",
            **kwargs,
        )
    
    async def create(self, description: str, **kwargs) -> SupervisorResult:
        """Shortcut for creative tasks."""
        return await self.run(
            task=f"Create: {description}",
            **kwargs,
        )


# Factory function for common patterns

def create_research_team(
    provider,
    api_key: str = None,
) -> Supervisor:
    """
    Create a pre-configured research team.
    
    Workers:
    - Researcher: Finds and gathers information
    - Analyst: Analyzes data and identifies patterns
    - Writer: Produces clear, readable content
    - Critic: Reviews and improves quality
    
    Example:
        team = create_research_team(provider="openai", api_key="sk-...")
        result = await team.run("What are the implications of quantum computing for cryptography?")
    """
    from ..agent import Agent
    
    workers = {
        "Researcher": Agent(
            role="Research specialist who finds comprehensive, accurate information on any topic. Focus on primary sources and recent developments.",
            name="Researcher",
            provider=provider,
            api_key=api_key,
            temperature=0.3,
        ),
        "Analyst": Agent(
            role="Data analyst who identifies patterns, trends, and insights from information. Provide structured analysis with clear reasoning.",
            name="Analyst",
            provider=provider,
            api_key=api_key,
            temperature=0.4,
        ),
        "Writer": Agent(
            role="Content writer who produces clear, engaging, well-structured content. Make complex topics accessible.",
            name="Writer",
            provider=provider,
            api_key=api_key,
            temperature=0.6,
        ),
        "Critic": Agent(
            role="Critical reviewer who identifies weaknesses, gaps, and areas for improvement. Be constructive but thorough.",
            name="Critic",
            provider=provider,
            api_key=api_key,
            temperature=0.3,
        ),
    }
    
    return Supervisor(
        workers=workers,
        config=SupervisorConfig(
            mode=WorkerSelectionMode.SELECTIVE,
            max_iterations=2,
            allow_replanning=True,
        ),
    )
