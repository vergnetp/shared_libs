"""
Node Agent - SSH-Free Deployment Infrastructure

This module provides:
- agent_code.py: The Flask app that runs on each droplet (embedded in snapshots)
- client.py: Client for making requests to node agents
"""

from .agent_code import NODE_AGENT_CODE, get_node_agent_install_script
from .client import NodeAgentClient

__all__ = ["NODE_AGENT_CODE", "get_node_agent_install_script", "NodeAgentClient"]
