"""Federation SDK for external agent integration.

This module provides:
- API key generation and validation for external agents
- Agent registration and approval workflow
- Federation token management for cross-agent authentication
- Sample SDK client for external agents

Usage:
    from src.federation import FederationAuth, AgentRegistry

    # Generate API key for external agent
    auth = FederationAuth()
    api_key = auth.create_api_key(agent_id="agent_xyz", org="acme_corp")

    # Validate incoming request
    agent_info = auth.validate_api_key(request_api_key)
"""

from src.federation.auth import FederationAuth, FederationTokenError
from src.federation.agent_registry import AgentRegistry, AgentRegistryError
from src.federation_core import (
    generate_agent_id,
    generate_api_key,
    hash_api_key,
    register_federated_agent,
    verify_federated_agent,
    create_agent_session,
    get_agent_session,
    heartbeat_session,
    cleanup_stale_sessions,
    join_debate_as_agent,
    FederatedAgent,
    AgentSession,
    FEDERATION_API_KEY_PREFIX,
    FEDERATION_TOKEN_LENGTH,
    _active_sessions,
)

__all__ = [
    "FederationAuth",
    "FederationTokenError",
    "AgentRegistry",
    "AgentRegistryError",
    "generate_agent_id",
    "generate_api_key",
    "hash_api_key",
    "register_federated_agent",
    "verify_federated_agent",
    "create_agent_session",
    "get_agent_session",
    "heartbeat_session",
    "cleanup_stale_sessions",
    "join_debate_as_agent",
    "FederatedAgent",
    "AgentSession",
    "FEDERATION_API_KEY_PREFIX",
    "FEDERATION_TOKEN_LENGTH",
    "_active_sessions",
]
