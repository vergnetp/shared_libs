"""
Pipeline pattern for sequential agent processing.

Pass output from one agent as input to the next.

Usage:
    from ai_agents.orchestration import Pipeline
    
    pipeline = Pipeline([
        researcher,   # Step 1: Research the topic
        writer,       # Step 2: Write based on research
        editor,       # Step 3: Edit and polish
    ])
    
    result = await pipeline.run("Write about AI trends")
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional, Callable
from dataclasses import dataclass, field


@dataclass
class PipelineStep:
    """Result from a single pipeline step."""
    agent_name: str
    input: str
    output: str
    success: bool = True
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class PipelineResult:
    """Result from pipeline execution."""
    content: str  # Final output
    success: bool = True
    steps: list[PipelineStep] = field(default_factory=list)
    total_duration_ms: int = 0
    total_cost: float = 0.0
    
    @property
    def failed_step(self) -> Optional[PipelineStep]:
        """Get the step that failed (if any)."""
        for step in self.steps:
            if not step.success:
                return step
        return None


class Pipeline:
    """
    Sequential agent processing pipeline.
    
    Each agent receives the output of the previous agent as input.
    Useful for multi-stage processing like: research → write → edit.
    
    Example:
        # Basic pipeline
        pipeline = Pipeline([agent1, agent2, agent3])
        result = await pipeline.run("Initial prompt")
        
        # With transformers between steps
        pipeline = Pipeline(
            agents=[researcher, writer, editor],
            transformers={
                1: lambda x: f"Based on this research, write an article:\\n{x}",
                2: lambda x: f"Edit and improve this draft:\\n{x}",
            }
        )
        
        # With conditional continuation
        pipeline = Pipeline(
            agents=[classifier, handler_a, handler_b],
            router=lambda output, step: 1 if "category_a" in output else 2,
        )
    """
    
    def __init__(
        self,
        agents: list,
        transformers: dict[int, Callable[[str], str]] = None,
        step_timeout: float = 120.0,
        stop_on_error: bool = True,
    ):
        """
        Args:
            agents: List of agents to run in sequence
            transformers: Dict mapping step index to transform function
                         Transform is applied to output before passing to next agent
            step_timeout: Timeout for each step in seconds
            stop_on_error: If True, stop pipeline on first error
        """
        self.agents = agents
        self.transformers = transformers or {}
        self.step_timeout = step_timeout
        self.stop_on_error = stop_on_error
    
    async def run(
        self,
        initial_input: str,
        user_id: str = "default",
        **kwargs,
    ) -> PipelineResult:
        """
        Run the pipeline.
        
        Args:
            initial_input: Initial input for first agent
            user_id: User ID for context
            
        Returns:
            PipelineResult with final output and all step details
        """
        import time
        start_time = time.time()
        
        steps = []
        current_input = initial_input
        total_cost = 0.0
        
        for i, agent in enumerate(self.agents):
            step_start = time.time()
            agent_name = getattr(agent, 'name', f"Agent_{i}")
            
            try:
                # Run agent with timeout
                output = await asyncio.wait_for(
                    agent.chat(current_input, user_id=user_id, **kwargs),
                    timeout=self.step_timeout,
                )
                
                duration_ms = int((time.time() - step_start) * 1000)
                
                # Track cost
                if hasattr(agent, 'conversation_cost'):
                    total_cost += agent.conversation_cost
                
                step = PipelineStep(
                    agent_name=agent_name,
                    input=current_input[:500] + "..." if len(current_input) > 500 else current_input,
                    output=output,
                    success=True,
                    duration_ms=duration_ms,
                )
                steps.append(step)
                
                # Apply transformer if defined for next step
                if i + 1 in self.transformers:
                    current_input = self.transformers[i + 1](output)
                else:
                    current_input = output
                
            except asyncio.TimeoutError:
                step = PipelineStep(
                    agent_name=agent_name,
                    input=current_input[:500],
                    output="",
                    success=False,
                    error="Timeout",
                    duration_ms=int((time.time() - step_start) * 1000),
                )
                steps.append(step)
                
                if self.stop_on_error:
                    return PipelineResult(
                        content="",
                        success=False,
                        steps=steps,
                        total_duration_ms=int((time.time() - start_time) * 1000),
                        total_cost=total_cost,
                    )
                    
            except Exception as e:
                step = PipelineStep(
                    agent_name=agent_name,
                    input=current_input[:500],
                    output="",
                    success=False,
                    error=str(e),
                    duration_ms=int((time.time() - step_start) * 1000),
                )
                steps.append(step)
                
                if self.stop_on_error:
                    return PipelineResult(
                        content="",
                        success=False,
                        steps=steps,
                        total_duration_ms=int((time.time() - start_time) * 1000),
                        total_cost=total_cost,
                    )
        
        total_duration = int((time.time() - start_time) * 1000)
        
        # Final output is the last successful step's output
        final_output = ""
        for step in reversed(steps):
            if step.success:
                final_output = step.output
                break
        
        return PipelineResult(
            content=final_output,
            success=all(s.success for s in steps),
            steps=steps,
            total_duration_ms=total_duration,
            total_cost=total_cost,
        )


class ConditionalPipeline:
    """
    Pipeline with conditional branching.
    
    Routes to different agents based on previous output.
    
    Example:
        pipeline = ConditionalPipeline(
            classifier=classifier_agent,
            branches={
                "technical": [tech_writer, tech_editor],
                "creative": [creative_writer, creative_editor],
                "default": [general_writer],
            },
            router=lambda output: "technical" if "code" in output.lower() else "creative",
        )
    """
    
    def __init__(
        self,
        classifier,
        branches: dict[str, list],
        router: Callable[[str], str],
        step_timeout: float = 120.0,
    ):
        """
        Args:
            classifier: Agent that classifies/analyzes input
            branches: Dict mapping branch names to agent lists
            router: Function that takes classifier output and returns branch name
            step_timeout: Timeout per step
        """
        self.classifier = classifier
        self.branches = branches
        self.router = router
        self.step_timeout = step_timeout
    
    async def run(
        self,
        initial_input: str,
        user_id: str = "default",
        **kwargs,
    ) -> PipelineResult:
        """Run conditional pipeline."""
        import time
        start_time = time.time()
        
        steps = []
        total_cost = 0.0
        
        # Step 1: Classify
        try:
            classifier_output = await asyncio.wait_for(
                self.classifier.chat(initial_input, user_id=user_id, **kwargs),
                timeout=self.step_timeout,
            )
            
            steps.append(PipelineStep(
                agent_name=getattr(self.classifier, 'name', 'Classifier'),
                input=initial_input[:500],
                output=classifier_output,
                success=True,
            ))
            
            if hasattr(self.classifier, 'conversation_cost'):
                total_cost += self.classifier.conversation_cost
                
        except Exception as e:
            return PipelineResult(
                content="",
                success=False,
                steps=[PipelineStep(
                    agent_name='Classifier',
                    input=initial_input[:500],
                    output="",
                    success=False,
                    error=str(e),
                )],
                total_duration_ms=int((time.time() - start_time) * 1000),
            )
        
        # Step 2: Route to branch
        branch_name = self.router(classifier_output)
        branch_agents = self.branches.get(branch_name, self.branches.get("default", []))
        
        if not branch_agents:
            return PipelineResult(
                content=classifier_output,
                success=True,
                steps=steps,
                total_duration_ms=int((time.time() - start_time) * 1000),
                total_cost=total_cost,
            )
        
        # Step 3: Run branch pipeline
        branch_pipeline = Pipeline(branch_agents, step_timeout=self.step_timeout)
        branch_result = await branch_pipeline.run(
            classifier_output,
            user_id=user_id,
            **kwargs,
        )
        
        steps.extend(branch_result.steps)
        total_cost += branch_result.total_cost
        
        return PipelineResult(
            content=branch_result.content,
            success=branch_result.success,
            steps=steps,
            total_duration_ms=int((time.time() - start_time) * 1000),
            total_cost=total_cost,
        )
