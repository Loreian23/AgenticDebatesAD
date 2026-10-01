"""Federation SDK - Agent authentication and registration."""

import hashlib
import secrets
import json
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from dataclasses import dataclass

from fastapi import HTTPException, Depends, Header, Query
from sqlalchemy.orm import Session

from src.database import get_db
from src.models import Participant, Debate, DebateStatus, ParticipantType, ParticipantSide


# ============== Constants ==============

FEDERATION_API_KEY_PREFIX = "fdk_live_"
FEDERATION_TOKEN_LENGTH = 32


# ============== Dataclasses ==============

@dataclass
class FederatedAgent:
    """Represents a registered federated agent."""
    agent_id: str
    api_key: str
    agent_name: str
    provider: str
    model: str
    capabilities: list
    webhook_url: Optional[str]
    registered_at: datetime
    is_active: bool = True


@dataclass
class AgentSession:
    """Active session for a federated agent in a debate."""
    session_id: str
    agent_id: str
    participant_id: str
    debate_id: str
    side: str
    joined_at: datetime
    last_heartbeat: datetime


# ============== API Key Generation ==============

def generate_agent_id() -> str:
    """Generate unique agent ID."""
    return f"agent_{secrets.token_hex(6)}_{secrets.token_hex(6)}"


def generate_api_key() -> str:
    """Generate federated API key."""
    random_part = secrets.token_urlsafe(FEDERATION_TOKEN_LENGTH)
    return f"{FEDERATION_API_KEY_PREFIX}{random_part}"


def hash_api_key(api_key: str) -> str:
    """Hash API key for storage (never store plain)."""
    return hashlib.sha256(api_key.encode()).hexdigest()


# ============== Agent Registration ==============

def register_federated_agent(
    agent_name: str,
    provider: str,
    model: str,
    capabilities: list,
    webhook_url: Optional[str],
    db: Session
) -> FederatedAgent:
    """
    Register a new federated agent.
    
    Args:
        agent_name: Display name for the agent
        provider: AI provider (openai, anthropic, custom, etc.)
        model: Model identifier
        capabilities: List of capabilities (opening, rebuttal, closing, judging)
        webhook_url: Optional callback URL
        db: Database session
    
    Returns:
        FederatedAgent with credentials
    
    Raises:
        HTTPException: If registration fails
    """
    # Validate capabilities
    valid_capabilities = {"opening", "rebuttal", "closing", "judging"}
    for cap in capabilities:
        if cap not in valid_capabilities:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid capability: {cap}. Valid: {valid_capabilities}"
            )
    
    # Validate provider
    valid_providers = {"openai", "anthropic", "google", "meta", "custom"}
    if provider not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid provider: {provider}. Valid: {valid_providers}"
        )
    
    # Generate credentials
    agent_id = generate_agent_id()
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)
    
    # Create participant record (agents are stored as participants)
    participant = Participant(
        id=agent_id,
        name=agent_name,
        participant_type=ParticipantType.AGENT,
        side=ParticipantSide.OBSERVER,  # Will be set on join
        agent_provider=provider,
        # Store hashed API key in metadata
        metadata_json=json.dumps({
            "api_key_hash": api_key_hash,
            "model": model,
            "capabilities": capabilities,
            "webhook_url": webhook_url,
            "provider": provider,
        })
    )
    
    db.add(participant)
    db.commit()
    db.refresh(participant)
    
    return FederatedAgent(
        agent_id=agent_id,
        api_key=api_key,
        agent_name=agent_name,
        provider=provider,
        model=model,
        capabilities=capabilities,
        webhook_url=webhook_url,
        registered_at=participant.joined_at,
    )


def verify_federated_agent(
    api_key: str,
    agent_id: str,
    db: Session
) -> bool:
    """
    Verify federated agent credentials.
    
    Args:
        api_key: Plain-text API key
        agent_id: Agent ID to verify
        db: Database session
    
    Returns:
        True if valid
    
    Raises:
        HTTPException: If verification fails
    """
    if not api_key.startswith(FEDERATION_API_KEY_PREFIX):
        raise HTTPException(
            status_code=401,
            detail="AGENT_NOT_FOUND",
            headers={"X-Error-Code": "AGENT_NOT_FOUND"}
        )
    
    api_key_hash = hash_api_key(api_key)
    
    participant = db.query(Participant).filter(
        Participant.id == agent_id,
        Participant.participant_type == ParticipantType.AGENT
    ).first()
    
    if not participant:
        raise HTTPException(
            status_code=401,
            detail="AGENT_NOT_FOUND",
            headers={"X-Error-Code": "AGENT_NOT_FOUND"}
        )
    
    # Verify hash
    metadata = json.loads(participant.metadata_json or "{}")
    if metadata.get("api_key_hash") != api_key_hash:
        raise HTTPException(
            status_code=401,
            detail="AGENT_NOT_FOUND",
            headers={"X-Error-Code": "AGENT_NOT_FOUND"}
        )
    
    return True


# ============== Agent Session Management ==============

_active_sessions: Dict[str, AgentSession] = {}


def create_agent_session(
    agent_id: str,
    participant_id: str,
    debate_id: str,
    side: str
) -> AgentSession:
    """Create a new agent session for a debate."""
    session_id = secrets.token_urlsafe(16)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    session = AgentSession(
        session_id=session_id,
        agent_id=agent_id,
        participant_id=participant_id,
        debate_id=debate_id,
        side=side,
        joined_at=now,
        last_heartbeat=now,
    )
    
    _active_sessions[session_id] = session
    return session


def get_agent_session(session_id: str) -> Optional[AgentSession]:
    """Get active session by ID."""
    return _active_sessions.get(session_id)


def heartbeat_session(session_id: str) -> bool:
    """Update session heartbeat."""
    if session_id in _active_sessions:
        _active_sessions[session_id].last_heartbeat = datetime.now(timezone.utc).replace(tzinfo=None)
        return True
    return False


def cleanup_stale_sessions(max_age_seconds: int = 3600):
    """Remove sessions older than max_age_seconds."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale = [
        sid for sid, sess in _active_sessions.items()
        if (now - sess.last_heartbeat).total_seconds() > max_age_seconds
    ]
    for sid in stale:
        del _active_sessions[sid]
    return len(stale)


# ============== Join Debate ==============

def join_debate_as_agent(
    agent_id: str,
    debate_id: str,
    side: str,
    preferred_name: Optional[str],
    db: Session
) -> Dict[str, Any]:
    """
    Join a debate as a federated agent.
    
    Returns debate state and participant info.
    """
    # Verify agent exists
    agent = db.query(Participant).filter(
        Participant.id == agent_id,
        Participant.participant_type == ParticipantType.AGENT
    ).first()
    
    if not agent:
        raise HTTPException(
            status_code=401,
            detail="AGENT_NOT_FOUND",
            headers={"X-Error-Code": "AGENT_NOT_FOUND"}
        )
    
    # Verify debate exists and is joinable
    debate = db.query(Debate).filter(Debate.id == debate_id).first()
    
    if not debate:
        raise HTTPException(
            status_code=404,
            detail="DEBATE_NOT_FOUND",
            headers={"X-Error-Code": "DEBATE_NOT_FOUND"}
        )
    
    if debate.status not in [DebateStatus.PENDING, DebateStatus.WAITING]:
        raise HTTPException(
            status_code=400,
            detail="DEBATE_NOT_JOINABLE",
            headers={"X-Error-Code": "DEBATE_NOT_JOINABLE"}
        )
    
    # Validate side
    try:
        side_enum = ParticipantSide(side)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="INVALID_SIDE",
            headers={"X-Error-Code": "INVALID_SIDE"}
        )
    
    # Create participant record for this debate
    participant = Participant(
        debate_id=debate_id,
        name=preferred_name or agent.name,
        participant_type=ParticipantType.AGENT,
        side=side_enum,
        agent_provider=agent.agent_provider,
    )
    
    db.add(participant)
    db.commit()
    db.refresh(participant)
    
    # Create session
    session = create_agent_session(
        agent_id=agent_id,
        participant_id=participant.id,
        debate_id=debate_id,
        side=side
    )
    
    return {
        "participant_id": participant.id,
        "debate_id": debate_id,
        "session_id": session.session_id,
        "debate_state": {
            "status": debate.status.value,
            "current_phase": debate.current_phase.value,
            "turn_count": len(debate.turns),
            "max_turn_length": debate.max_turn_length,
            "max_turn_time_seconds": debate.max_turn_time_seconds,
        },
        "joined_at": session.joined_at.isoformat(),
    }
