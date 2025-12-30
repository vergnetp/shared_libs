"""
App-specific analytics and usage endpoints.

Provides business metrics:
- /analytics/metrics - Entity counts, costs, token usage
- /analytics/usage - Usage breakdown with budgets
- /analytics/llm-calls - LLM call audit log
- /analytics/security/* - Security events

Note: Infrastructure endpoints are in kernel:
- /healthz, /readyz - Health checks (kernel)
- /metrics - Prometheus metrics (kernel)
- /jobs/* - Job management (kernel)
- /pools - DB pool metrics (kernel)
"""

import time
import json
from fastapi import APIRouter, Depends
from typing import Optional

from ..deps import (
    get_db,
    get_cost_tracker,
    get_security_log,
    CostTracker,
    SecurityAuditLog,
)
from ..schemas import MetricsResponse, UsageResponse, CostBreakdown
from ...config import get_settings

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _parse_json(val, default=None):
    """Parse JSON string or return as-is if already parsed."""
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return default


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics(
    db=Depends(get_db),
):
    """
    Get app-level service metrics.
    
    Includes entity counts, costs, and token usage from assistant messages.
    
    Note: For Prometheus scraping, use /metrics (kernel, admin-protected).
    """
    # Count entities
    agents = await db.find_entities("agents", limit=10000)
    threads = await db.find_entities("threads", limit=10000)
    messages = await db.find_entities("messages", limit=10000)
    documents = await db.find_entities("documents", limit=10000)
    
    # Calculate totals from assistant messages
    total_cost = 0.0
    total_tokens = 0
    llm_calls = 0
    
    for msg in (messages or []):
        if msg.get("role") == "assistant":
            metadata = _parse_json(msg.get("metadata"), {})
            if metadata:
                total_cost += metadata.get("cost", 0) or 0
                usage = metadata.get("usage", {})
                total_tokens += (usage.get("input", 0) or 0) + (usage.get("output", 0) or 0)
                if metadata.get("call_type"):
                    llm_calls += 1
    
    return MetricsResponse(
        total_requests=llm_calls,
        total_tokens=total_tokens,
        total_cost=total_cost,
        agents=len(agents) if agents else 0,
        threads=len(threads) if threads else 0,
        messages=len(messages) if messages else 0,
        documents=len(documents) if documents else 0,
    )


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    period: str = "day",  # day, week, month
    cost_tracker: CostTracker = Depends(get_cost_tracker),
):
    """
    Get usage statistics with cost breakdown.
    """
    settings = get_settings()
    
    # CostTracker doesn't track per-model breakdown, so provide aggregate
    metrics = cost_tracker.to_dict()
    total_tokens = metrics.get("total_tokens", {})
    
    breakdown = [
        CostBreakdown(
            provider="all",
            model="aggregate",
            input_tokens=total_tokens.get("input", 0),
            output_tokens=total_tokens.get("output", 0),
            cost=metrics.get("total_cost", 0),
        )
    ] if metrics.get("total_cost", 0) > 0 else []
    
    total_cost = metrics.get("total_cost", 0)
    budget_remaining = settings.total_budget - total_cost
    budget_used_percent = (total_cost / settings.total_budget * 100) if settings.total_budget > 0 else 0
    
    return UsageResponse(
        period=period,
        total_cost=total_cost,
        breakdown=breakdown,
        budget_remaining=max(0, budget_remaining),
        budget_used_percent=min(100, budget_used_percent),
    )


@router.get("/llm-calls")
async def get_llm_calls(
    limit: int = 100,
    call_type: Optional[str] = None,
    db=Depends(get_db),
):
    """
    Get LLM call audit log from assistant messages.
    
    Args:
        limit: Max number of calls to return (default 100)
        call_type: Filter by call type (chat, chat_stream, chat_ws)
    """
    # Get assistant messages with metadata
    messages = await db.find_entities(
        "messages",
        where_clause="[role] = ?",
        params=("assistant",),
        order_by="created_at DESC",
        limit=limit * 2,  # Get more to filter
    )
    
    # Cache for thread -> agent lookups
    thread_agent_cache = {}
    agent_name_cache = {}
    
    async def get_agent_name(thread_id):
        if not thread_id:
            return None
        if thread_id in thread_agent_cache:
            agent_id = thread_agent_cache[thread_id]
        else:
            thread = await db.get_entity("threads", thread_id)
            agent_id = thread.get("agent_id") if thread else None
            thread_agent_cache[thread_id] = agent_id
        
        if not agent_id:
            return None
        if agent_id in agent_name_cache:
            return agent_name_cache[agent_id]
        
        agent = await db.get_entity("agents", agent_id)
        name = agent.get("name") if agent else None
        agent_name_cache[agent_id] = name
        return name
    
    calls = []
    total_cost = 0.0
    total_input = 0
    total_output = 0
    by_type = {}
    by_model = {}
    
    for msg in (messages or []):
        metadata = _parse_json(msg.get("metadata"), {})
        if not metadata or not metadata.get("call_type"):
            continue
        
        msg_call_type = metadata.get("call_type", "")
        if call_type and msg_call_type != call_type:
            continue
        
        usage = metadata.get("usage", {})
        input_tokens = usage.get("input", 0) or 0
        output_tokens = usage.get("output", 0) or 0
        cost = metadata.get("cost", 0) or 0
        
        thread_id = msg.get("thread_id", "")
        agent_name = await get_agent_name(thread_id)
        
        call = {
            "timestamp": msg.get("created_at"),
            "provider": metadata.get("provider", ""),
            "model": metadata.get("model", ""),
            "call_type": msg_call_type,
            "message_preview": (msg.get("content") or "")[:100],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tools_used": metadata.get("tools_used", []),
            "cost": cost,
            "duration_ms": metadata.get("duration_ms", 0),
            "thread_id": thread_id,
            "agent_name": agent_name,
        }
        
        calls.append(call)
        
        # Aggregate
        total_cost += cost
        total_input += input_tokens
        total_output += output_tokens
        
        # By type
        if msg_call_type not in by_type:
            by_type[msg_call_type] = {"count": 0, "cost": 0.0, "input": 0, "output": 0}
        by_type[msg_call_type]["count"] += 1
        by_type[msg_call_type]["cost"] += cost
        by_type[msg_call_type]["input"] += input_tokens
        by_type[msg_call_type]["output"] += output_tokens
        
        # By model
        model = metadata.get("model", "unknown")
        if model not in by_model:
            by_model[model] = {"count": 0, "cost": 0.0, "input": 0, "output": 0}
        by_model[model]["count"] += 1
        by_model[model]["cost"] += cost
        by_model[model]["input"] += input_tokens
        by_model[model]["output"] += output_tokens
        
        if len(calls) >= limit:
            break
    
    return {
        "calls": calls,
        "summary": {
            "total_calls": len(calls),
            "total_cost": total_cost,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "by_type": by_type,
            "by_model": by_model,
        },
    }


@router.get("/security/events")
async def get_security_events(
    limit: int = 100,
    threat_type: Optional[str] = None,
    security_log: SecurityAuditLog = Depends(get_security_log),
):
    """
    Get security audit events.
    
    Includes: rate limits, blocked requests, errors, etc.
    """
    events = security_log.get_events(threat_type=threat_type, limit=limit)
    
    return {
        "events": [e.to_dict() for e in events],
        "total": len(events),
    }


@router.get("/security/summary")
async def get_security_summary(
    security_log: SecurityAuditLog = Depends(get_security_log),
):
    """
    Get security summary.
    
    Aggregated counts by event type.
    """
    return security_log.get_report()


@router.post("/security/test")
async def run_security_test(
    agent_id: str,
    db=Depends(get_db),
):
    """
    Run security audit on an agent.
    
    Tests for jailbreaking vulnerabilities.
    """
    from ..deps import AgentStore, run_security_audit, AgentDefinition, Agent
    
    settings = get_settings()
    
    # Get agent
    store = AgentStore(db)
    agent_data = await store.get(agent_id)
    
    if not agent_data:
        return {"error": f"Agent not found: {agent_id}"}
    
    # Build agent
    definition = AgentDefinition(
        name=agent_data.get("name", "Agent"),
        role=agent_data.get("role", "Assistant"),
        provider=agent_data.get("provider", settings.default_provider),
        model=agent_data.get("model", settings.default_model),
    )
    
    api_key = (
        settings.anthropic_api_key 
        if agent_data.get("provider") == "anthropic" 
        else settings.openai_api_key
    )
    
    agent = Agent(definition=definition, api_key=api_key)
    
    # Run audit
    report = await run_security_audit(agent)
    
    return {
        "agent_id": agent_id,
        "passed": report.passed,
        "total_tests": report.total_tests,
        "passed_tests": report.passed_tests,
        "failed_tests": report.failed_tests,
        "vulnerabilities": report.vulnerabilities,
    }


@router.post("/reset-cost-tracker")
async def reset_cost_tracker(
    cost_tracker: CostTracker = Depends(get_cost_tracker),
):
    """
    Reset cost tracking (for testing).
    """
    cost_tracker.reset_conversation()
    
    return {"status": "reset"}
