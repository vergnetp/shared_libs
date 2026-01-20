"""
Debate pattern for agent discussion and consensus.

Multiple agents discuss a topic, respond to each other, and reach conclusions.

Usage:
    from ai_agents.orchestration import Debate
    
    debate = Debate(
        agents=[optimist, pessimist, pragmatist],
        moderator=moderator_agent,  # Optional
        rounds=3,
    )
    
    result = await debate.run("Should we adopt AI in healthcare?")
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from dataclasses import dataclass, field


@dataclass
class DebateMessage:
    """Single message in the debate."""
    agent_name: str
    content: str
    round: int
    is_response_to: Optional[str] = None  # Agent being responded to


@dataclass
class DebateResult:
    """Result from debate execution."""
    conclusion: str
    consensus_reached: bool = False
    messages: list[DebateMessage] = field(default_factory=list)
    rounds_completed: int = 0
    total_duration_ms: int = 0
    total_cost: float = 0.0
    
    @property
    def by_agent(self) -> dict[str, list[DebateMessage]]:
        """Group messages by agent."""
        result = {}
        for msg in self.messages:
            if msg.agent_name not in result:
                result[msg.agent_name] = []
            result[msg.agent_name].append(msg)
        return result
    
    @property
    def by_round(self) -> dict[int, list[DebateMessage]]:
        """Group messages by round."""
        result = {}
        for msg in self.messages:
            if msg.round not in result:
                result[msg.round] = []
            result[msg.round].append(msg)
        return result
    
    def to_transcript(self) -> str:
        """Format as readable transcript."""
        lines = []
        current_round = 0
        
        for msg in self.messages:
            if msg.round != current_round:
                current_round = msg.round
                lines.append(f"\n--- Round {current_round} ---\n")
            
            lines.append(f"**{msg.agent_name}**:")
            lines.append(msg.content)
            lines.append("")
        
        if self.conclusion:
            lines.append("\n--- Conclusion ---\n")
            lines.append(self.conclusion)
        
        return "\n".join(lines)


class Debate:
    """
    Multi-agent debate for exploring topics from multiple perspectives.
    
    Flow:
    1. Each agent gives initial position (parallel)
    2. For each round:
       - Agents respond to others' points (parallel)
    3. Moderator (or synthesizer) draws conclusions
    
    Example:
        # Simple debate
        debate = Debate(
            agents=[bull, bear, analyst],
            rounds=2,
        )
        result = await debate.run("Will Bitcoin reach $100k this year?")
        
        # With moderator
        debate = Debate(
            agents=[optimist, pessimist, realist],
            moderator=moderator,
            rounds=3,
            require_consensus=True,
        )
    """
    
    def __init__(
        self,
        agents: list,
        moderator: Any = None,
        rounds: int = 2,
        parallel_responses: bool = True,
        require_consensus: bool = False,
        round_timeout: float = 180.0,
    ):
        """
        Args:
            agents: Debating agents
            moderator: Agent to moderate and conclude (optional)
            rounds: Number of response rounds
            parallel_responses: Run responses in parallel (faster)
            require_consensus: Keep debating until consensus
            round_timeout: Timeout per round
        """
        self.agents = agents
        self.moderator = moderator
        self.rounds = rounds
        self.parallel_responses = parallel_responses
        self.require_consensus = require_consensus
        self.round_timeout = round_timeout
        
        # Lazy init synthesizer if no moderator
        self._synthesizer = None
    
    async def _get_synthesizer(self):
        """Get or create synthesizer for conclusions."""
        if self.moderator:
            return self.moderator
        
        if self._synthesizer is None:
            from ..agent import Agent
            
            # Get provider from first agent
            first_agent = self.agents[0]
            provider = getattr(first_agent, '_provider', None)
            
            self._synthesizer = Agent(
                role="""You are a debate moderator who synthesizes multiple viewpoints into balanced conclusions.

Your job:
1. Identify key points of agreement
2. Note remaining disagreements
3. Provide a balanced conclusion that acknowledges all perspectives
4. State whether consensus was reached""",
                name="Moderator",
                _provider=provider,
                temperature=0.4,
            )
        
        return self._synthesizer
    
    async def run(
        self,
        topic: str,
        user_id: str = "default",
        context: str = "",
    ) -> DebateResult:
        """
        Run the debate.
        
        Args:
            topic: Topic to debate
            user_id: User ID for context
            context: Additional context
            
        Returns:
            DebateResult with transcript and conclusion
        """
        import time
        start_time = time.time()
        
        messages = []
        total_cost = 0.0
        
        # Round 0: Initial positions
        initial_prompt = f"Topic: {topic}"
        if context:
            initial_prompt += f"\n\nContext: {context}"
        initial_prompt += "\n\nState your position on this topic clearly and concisely."
        
        round_0_messages = await self._run_round(
            round_num=0,
            prompt=initial_prompt,
            previous_messages=[],
            user_id=user_id,
        )
        messages.extend(round_0_messages)
        
        # Update costs
        for agent in self.agents:
            if hasattr(agent, 'conversation_cost'):
                total_cost += agent.conversation_cost
        
        # Response rounds
        for round_num in range(1, self.rounds + 1):
            # Build prompt with previous round's messages
            debate_context = self._format_debate_context(messages, round_num - 1)
            
            prompt = f"""Topic: {topic}

Previous discussion:
{debate_context}

Respond to the other participants' points. You may:
- Challenge their arguments
- Support points you agree with
- Introduce new considerations
- Modify your position based on valid arguments

Be concise and focused."""
            
            round_messages = await self._run_round(
                round_num=round_num,
                prompt=prompt,
                previous_messages=messages,
                user_id=user_id,
            )
            messages.extend(round_messages)
            
            # Update costs
            for agent in self.agents:
                if hasattr(agent, 'conversation_cost'):
                    total_cost += agent.conversation_cost
        
        # Conclude
        synthesizer = await self._get_synthesizer()
        conclusion_prompt = f"""Topic: {topic}

Full debate transcript:
{self._format_debate_context(messages, include_all=True)}

Provide a balanced conclusion that:
1. Summarizes key points from each perspective
2. Identifies areas of agreement and disagreement
3. States whether consensus was reached
4. Offers a final balanced assessment"""
        
        try:
            conclusion = await asyncio.wait_for(
                synthesizer.chat(conclusion_prompt, user_id=user_id),
                timeout=self.round_timeout,
            )
            
            if hasattr(synthesizer, 'conversation_cost'):
                total_cost += synthesizer.conversation_cost
                
        except Exception as e:
            conclusion = f"Failed to generate conclusion: {e}"
        
        # Check for consensus (simple heuristic)
        consensus = self._check_consensus(messages, conclusion)
        
        total_duration = int((time.time() - start_time) * 1000)
        
        return DebateResult(
            conclusion=conclusion,
            consensus_reached=consensus,
            messages=messages,
            rounds_completed=self.rounds,
            total_duration_ms=total_duration,
            total_cost=total_cost,
        )
    
    async def _run_round(
        self,
        round_num: int,
        prompt: str,
        previous_messages: list[DebateMessage],
        user_id: str,
    ) -> list[DebateMessage]:
        """Run a single debate round."""
        
        if self.parallel_responses:
            # Parallel execution
            async def get_response(agent) -> DebateMessage:
                try:
                    name = getattr(agent, 'name', 'Agent')
                    response = await agent.chat(prompt, user_id=user_id)
                    return DebateMessage(
                        agent_name=name,
                        content=response,
                        round=round_num,
                    )
                except Exception as e:
                    name = getattr(agent, 'name', 'Agent')
                    return DebateMessage(
                        agent_name=name,
                        content=f"[Error: {e}]",
                        round=round_num,
                    )
            
            tasks = [get_response(agent) for agent in self.agents]
            
            try:
                messages = await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=self.round_timeout,
                )
                return list(messages)
            except asyncio.TimeoutError:
                return [
                    DebateMessage(
                        agent_name=getattr(a, 'name', 'Agent'),
                        content="[Timeout]",
                        round=round_num,
                    )
                    for a in self.agents
                ]
        
        else:
            # Sequential execution
            messages = []
            for agent in self.agents:
                try:
                    name = getattr(agent, 'name', 'Agent')
                    response = await asyncio.wait_for(
                        agent.chat(prompt, user_id=user_id),
                        timeout=self.round_timeout / len(self.agents),
                    )
                    messages.append(DebateMessage(
                        agent_name=name,
                        content=response,
                        round=round_num,
                    ))
                except Exception as e:
                    name = getattr(agent, 'name', 'Agent')
                    messages.append(DebateMessage(
                        agent_name=name,
                        content=f"[Error: {e}]",
                        round=round_num,
                    ))
            return messages
    
    def _format_debate_context(
        self,
        messages: list[DebateMessage],
        round_num: int = None,
        include_all: bool = False,
    ) -> str:
        """Format messages for context."""
        lines = []
        
        for msg in messages:
            if include_all or (round_num is not None and msg.round == round_num):
                lines.append(f"**{msg.agent_name}** (Round {msg.round}):")
                lines.append(msg.content)
                lines.append("")
        
        return "\n".join(lines)
    
    def _check_consensus(
        self,
        messages: list[DebateMessage],
        conclusion: str,
    ) -> bool:
        """Simple heuristic to check if consensus was reached."""
        # Check if conclusion mentions consensus
        consensus_indicators = [
            "consensus reached",
            "agreement was reached",
            "all participants agree",
            "unanimous",
            "converged on",
        ]
        
        conclusion_lower = conclusion.lower()
        for indicator in consensus_indicators:
            if indicator in conclusion_lower:
                return True
        
        # Check for explicit "no consensus"
        no_consensus_indicators = [
            "no consensus",
            "disagreement remains",
            "failed to reach agreement",
            "positions remain",
        ]
        
        for indicator in no_consensus_indicators:
            if indicator in conclusion_lower:
                return False
        
        # Default: no consensus determined
        return False


# Factory functions for common debate setups

def create_pros_cons_debate(
    provider,
    api_key: str = None,
    rounds: int = 2,
) -> Debate:
    """
    Create a pros/cons debate with advocate and critic.
    
    Example:
        debate = create_pros_cons_debate(provider="openai", api_key="sk-...")
        result = await debate.run("Should remote work be permanent?")
    """
    from ..agent import Agent
    
    advocate = Agent(
        role="""You are an advocate who argues FOR positions. Your job is to:
- Present the strongest arguments in favor
- Highlight benefits and opportunities
- Counter criticisms constructively
Be persuasive but honest.""",
        name="Advocate",
        provider=provider,
        api_key=api_key,
        temperature=0.6,
    )
    
    critic = Agent(
        role="""You are a critic who argues AGAINST positions. Your job is to:
- Present the strongest arguments against
- Highlight risks and downsides
- Challenge assumptions and claims
Be rigorous but fair.""",
        name="Critic",
        provider=provider,
        api_key=api_key,
        temperature=0.6,
    )
    
    pragmatist = Agent(
        role="""You are a pragmatist who seeks practical middle ground. Your job is to:
- Identify valid points from both sides
- Propose practical compromises
- Focus on implementation realities
Be balanced and constructive.""",
        name="Pragmatist",
        provider=provider,
        api_key=api_key,
        temperature=0.5,
    )
    
    return Debate(
        agents=[advocate, critic, pragmatist],
        rounds=rounds,
    )


def create_expert_panel(
    provider,
    expert_roles: list[str],
    api_key: str = None,
    rounds: int = 2,
) -> Debate:
    """
    Create a panel of experts with custom roles.
    
    Example:
        debate = create_expert_panel(
            provider="anthropic",
            api_key="sk-ant-...",
            expert_roles=[
                "Economist focusing on market dynamics",
                "Sociologist focusing on social impact",
                "Technologist focusing on technical feasibility",
            ],
        )
        result = await debate.run("Impact of UBI on society")
    """
    from ..agent import Agent
    
    agents = []
    for i, role in enumerate(expert_roles):
        agent = Agent(
            role=f"You are an expert: {role}. Provide insights from your domain expertise.",
            name=f"Expert_{i+1}",
            provider=provider,
            api_key=api_key,
            temperature=0.5,
        )
        agents.append(agent)
    
    return Debate(agents=agents, rounds=rounds)
