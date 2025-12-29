"""Cascading provider - fast model with premium escalation."""
from __future__ import annotations

from typing import AsyncIterator

from ..core import ProviderResponse
from ..costs import is_premium_model, calculate_cost as calc_cost
from .base import LLMProvider


# Default escalation instructions added to system prompt
ESCALATION_INSTRUCTIONS = """

## COMPLEXITY SELF-ASSESSMENT

After formulating your response, assess if this query needs deeper analysis.

THINK MORE when:
- Financial decisions (refunds, compensation, pricing disputes)
- Legal or liability implications
- Safety concerns
- User frustration, complaints, or emotional distress
- Ambiguous situations with multiple valid interpretations
- You feel uncertain about your answer
- Policy edge cases

If deeper thinking is needed:
1. Respond with empathetic acknowledgment ONLY - do NOT give substantive answer
2. End with [THINKING_MORE]

Example responses requiring escalation:
- "I understand this is frustrating. Let me think about this more carefully... [THINKING_MORE]"
- "That's an important question about your refund. Let me analyze this properly... [THINKING_MORE]"

CRITICAL: When escalating, NEVER provide the actual answer - only acknowledge and indicate you're thinking more.
"""

# Transition text that replaces the trigger
TRANSITION_TEXT = "\n\nLet me think about this more carefully...\n\n"


class CascadingProvider(LLMProvider):
    """
    Provider that uses fast model first, escalates to premium if needed.
    
    Fast model includes instructions to output [THINKING_MORE] for complex queries.
    When detected, premium model is called for deeper analysis.
    """
    
    name = "cascading"
    
    def __init__(
        self,
        fast: LLMProvider,
        premium: LLMProvider,
        trigger: str = "[THINKING_MORE]",
        transition: str = TRANSITION_TEXT,
        escalation_prompt: str = ESCALATION_INSTRUCTIONS,
    ):
        """
        Initialize cascading provider.
        
        Args:
            fast: Fast/cheap model for initial response
            premium: Premium model for complex queries
            trigger: Text that triggers escalation
            transition: Text to replace trigger with (for smooth UX)
            escalation_prompt: Instructions appended to system prompt
        """
        self.fast = fast
        self.premium = premium
        self.trigger = trigger
        self.transition = transition
        self.escalation_prompt = escalation_prompt
        
        # Track which model was used
        self.last_escalated = False
        self.last_models_used = []
    
    @property
    def model(self) -> str:
        """Return fast model name (primary)."""
        return self.fast.model
    
    def _should_inject_escalation(self) -> bool:
        """Check if escalation instructions should be injected."""
        # Don't inject if fast model is already premium
        if is_premium_model(self.fast.model):
            return False
        # Don't inject if fast == premium (no cascade configured)
        if self.fast.model == self.premium.model:
            return False
        return True
    
    def _inject_escalation_prompt(self, system: str) -> str:
        """Add escalation instructions to system prompt if needed."""
        if not self._should_inject_escalation():
            return system
        return (system or "") + self.escalation_prompt
    
    async def complete(
        self,
        messages: list[dict],
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        """
        Complete with fast model, escalate to premium if triggered.
        
        For non-streaming, we simply call both if needed and return premium response.
        """
        self.last_escalated = False
        self.last_models_used = [self.fast.model]
        
        # Inject escalation prompt
        enhanced_system = self._inject_escalation_prompt(system)
        
        # Fast model first
        fast_response = await self.fast.complete(
            messages=messages,
            system=enhanced_system,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            **kwargs,
        )
        
        # Check for escalation trigger
        if self.trigger not in fast_response.content:
            return fast_response
        
        # Escalate to premium
        self.last_escalated = True
        self.last_models_used.append(self.premium.model)
        
        # Premium sees original messages only (no fast model's incomplete response)
        # This lets premium respond directly and fully to the user's query
        premium_messages = messages.copy()
        
        # Premium model responds directly with full system prompt context
        premium_response = await self.premium.complete(
            messages=premium_messages,
            system=system,  # Original system prompt with agent's role/persona
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            **kwargs,
        )
        
        # Calculate actual costs for both models
        fast_cost = calc_cost(
            self.fast.model,
            fast_response.usage.get("input", 0),
            fast_response.usage.get("output", 0)
        )
        premium_cost = calc_cost(
            self.premium.model,
            premium_response.usage.get("input", 0),
            premium_response.usage.get("output", 0)
        )
        total_cost = fast_cost + premium_cost
        
        # Return only premium response with combined usage
        return ProviderResponse(
            content=premium_response.content,
            usage={
                "input": fast_response.usage.get("input", 0) + premium_response.usage.get("input", 0),
                "output": fast_response.usage.get("output", 0) + premium_response.usage.get("output", 0),
                "cost": total_cost,  # Pre-calculated accurate cost
                "fast_usage": fast_response.usage,
                "premium_usage": premium_response.usage,
            },
            model=f"{self.fast.model}+{self.premium.model}",
            provider=self.name,
            tool_calls=premium_response.tool_calls,
            finish_reason=premium_response.finish_reason,
            raw={"fast": fast_response.raw, "premium": premium_response.raw},
        )
    
    async def stream(
        self,
        messages: list[dict],
        system: str = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Stream with fast model, escalate to premium if triggered.
        
        Strategy: Buffer last N chars to detect trigger without breaking stream.
        If trigger found, replace with transition text and stream premium.
        """
        self.last_escalated = False
        self.last_models_used = [self.fast.model]
        
        # Inject escalation prompt
        enhanced_system = self._inject_escalation_prompt(system)
        
        full_response = ""
        pending = ""
        trigger_buffer_size = len(self.trigger) + 10  # Extra buffer for safety
        
        async for chunk in self.fast.stream(
            messages=messages,
            system=enhanced_system,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            **kwargs,
        ):
            full_response += chunk
            pending += chunk
            
            # Yield all but last N chars (to catch trigger that might span chunks)
            if len(pending) > trigger_buffer_size:
                yield_text = pending[:-trigger_buffer_size]
                yield yield_text
                pending = pending[-trigger_buffer_size:]
        
        # Fast model done - check pending buffer for trigger
        if self.trigger in pending:
            # Escalation triggered
            self.last_escalated = True
            self.last_models_used.append(self.premium.model)
            
            # Yield transition text (replace trigger with smooth transition)
            yield self.transition
            
            # Premium sees original messages only (not fast's incomplete response)
            # This lets premium respond directly with full context
            premium_messages = messages.copy()
            
            # Stream premium response
            async for chunk in self.premium.stream(
                messages=premium_messages,
                system=system,  # Original system prompt
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                **kwargs,
            ):
                yield chunk
        else:
            # No escalation - yield remaining buffer
            yield pending
    
    def count_tokens(self, messages: list[dict]) -> int:
        """Estimate tokens using fast model's counter."""
        return self.fast.count_tokens(messages)
    
    @property
    def max_context_tokens(self) -> int:
        """Return fast model's context limit."""
        return self.fast.max_context_tokens
    
    async def run(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict] = None,
        **kwargs,
    ) -> ProviderResponse:
        """Run completion (delegates to complete without system param)."""
        return await self.complete(
            messages=messages,
            system=None,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            **kwargs,
        )
