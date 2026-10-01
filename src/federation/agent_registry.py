"""Agent Registry for external federation agents.

Manages registration, approval, and profile information for external
AI agents that connect to the Agent Debate platform via the Federation SDK.

Workflow:
1. External agent registers via API with metadata
2. Platform admin approves/rejects registration
3. On approval, agent receives API key for authentication
4. Agent can then join debates via invite tokens

Approval levels:
- PENDING: Awaiting admin review
- APPROVED: Can participate in debates
- SUSPENDED: Temporarily blocked
- REJECTED: Permanently denied
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from enum import Enum

from sqlalchemy.orm import Session

from src.database import get_db_session, Base
from sqlalchemy import Column, String, Integer, DateTime, Boolean, JSON, Text, Index, Enum as SQLEnum, Float


class AgentApprovalStatus(str, Enum):
    """Registration approval status."""
    PENDING = "pending"
    APPROVED = "approved"
    SUSPENDED = "suspended"
    REJECTED = "rejected"


class RegisteredAgent(Base):
    """External agent registration record.
    
    Stores agent metadata and approval status.
    Actual authentication uses FederationAPIKey (separate table).
    """
    __tablename__ = "registered_agents"
    
    id = Column(String(36), primary_key=True)
    
    # Identity
    agent_id = Column(String(255), nullable=False, unique=True, index=True)
    agent_name = Column(String(255), nullable=False)
    agent_version = Column(String(50), nullable=True)
    
    # Organization
    org_id = Column(String(255), nullable=True, index=True)
    org_name = Column(String(255), nullable=True)
    
    # Capabilities (JSON for flexibility)
    capabilities = Column(JSON, default=dict)  # e.g., {"debate_formats": ["standard", "cross_exam"], "max_chars": 2000}
    supported_protocols = Column(JSON, default=list)  # e.g., ["v1", "v2"]
    
    # Contact (for admin notifications)
    contact_email = Column(String(255), nullable=True)
    webhook_url = Column(String(500), nullable=True)  # For callbacks
    
    # Approval
    status = Column(String(20), default=AgentApprovalStatus.PENDING.value, index=True)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(255), nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    rejected_by = Column(String(255), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    
    # Suspension
    suspended_at = Column(DateTime, nullable=True)
    suspended_by = Column(String(255), nullable=True)
    suspension_reason = Column(Text, nullable=True)
    
    # Metadata
    description = Column(Text, nullable=True)
    website = Column(String(500), nullable=True)
    logo_url = Column(String(500), nullable=True)
    
    # Stats
    debate_count = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    avg_score = Column(Float, nullable=True)
    last_debate_at = Column(DateTime, nullable=True)
    
    # Registration
    registered_at = Column(DateTime, default=datetime.utcnow)
    registered_ip = Column(String(45), nullable=True)
    registered_user_agent = Column(Text, nullable=True)
    
    # Updated
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # API key reference
    api_key_id = Column(String(36), nullable=True)  # Link to FederationAPIKey
    
    __table_args__ = (
        Index('idx_reg_agent_id', 'agent_id'),
        Index('idx_reg_org', 'org_id'),
        Index('idx_reg_status', 'status'),
    )


class AgentRegistryError(Exception):
    """Error with agent registry operations."""
    pass


class AgentRegistry:
    """Manage external agent registration and profiles.
    
    Usage:
        registry = AgentRegistry()
        
        # Register a new external agent
        result = registry.register_agent(
            agent_id="agent_veronica_v1",
            agent_name="Veronica",
            org_id="openclaw",
            capabilities={"debate_formats": ["standard"]},
            contact_email="veronica@example.com"
        )
        
        # Admin approves
        registry.approve_agent(agent_id="agent_veronica_v1", approved_by="admin")
        
        # Get agent info
        agent = registry.get_agent("agent_veronica_v1")
    """
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or get_db_session()
    
    def register_agent(
        self,
        agent_id: str,
        agent_name: str,
        org_id: Optional[str] = None,
        org_name: Optional[str] = None,
        agent_version: Optional[str] = None,
        capabilities: Optional[Dict[str, Any]] = None,
        supported_protocols: Optional[List[str]] = None,
        contact_email: Optional[str] = None,
        webhook_url: Optional[str] = None,
        description: Optional[str] = None,
        website: Optional[str] = None,
        registered_ip: Optional[str] = None,
        registered_user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a new external agent.
        
        Returns registration result with pending status.
        Agent cannot participate until admin approval.
        """
        from src.federation.auth import FederationAuth
        
        # Check if already registered
        existing = self.get_agent(agent_id)
        if existing:
            if existing["status"] == AgentApprovalStatus.REJECTED.value:
                raise AgentRegistryError(f"Agent {agent_id} was previously rejected")
            if existing["status"] == AgentApprovalStatus.SUSPENDED.value:
                raise AgentRegistryError(f"Agent {agent_id} is currently suspended")
            # Already registered and not rejected/suspended
            return existing
        
        # Create registration record
        agent = RegisteredAgent(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            agent_name=agent_name,
            org_id=org_id,
            org_name=org_name,
            agent_version=agent_version,
            capabilities=capabilities or {},
            supported_protocols=supported_protocols or ["v1"],
            contact_email=contact_email,
            webhook_url=webhook_url,
            description=description,
            website=website,
            registered_ip=registered_ip,
            registered_user_agent=registered_user_agent,
        )
        
        self.db.add(agent)
        self.db.commit()
        self.db.refresh(agent)
        
        return self._to_dict(agent)
    
    def approve_agent(
        self,
        agent_id: str,
        approved_by: str = "system",
        generate_api_key: bool = True,
    ) -> Dict[str, Any]:
        """Approve a registered agent and optionally generate API key.
        
        Returns updated agent info including API key if generated.
        """
        from src.federation.auth import FederationAuth
        
        agent = self.db.query(RegisteredAgent).filter(
            RegisteredAgent.agent_id == agent_id
        ).first()
        
        if not agent:
            raise AgentRegistryError(f"Agent not found: {agent_id}")
        
        if agent.status == AgentApprovalStatus.APPROVED.value:
            raise AgentRegistryError(f"Agent {agent_id} is already approved")
        
        if agent.status == AgentApprovalStatus.REJECTED.value:
            raise AgentRegistryError(f"Agent {agent_id} was rejected and cannot be re-approved")
        
        # Update status
        agent.status = AgentApprovalStatus.APPROVED.value
        agent.approved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        agent.approved_by = approved_by
        
        self.db.commit()
        self.db.refresh(agent)
        
        result = self._to_dict(agent)
        
        # Generate API key if requested
        if generate_api_key:
            auth = FederationAuth(self.db)
            prefix, full_key = auth.create_api_key(
                agent_id=agent_id,
                org_id=agent.org_id,
                name=f"API Key for {agent.agent_name}",
                created_by=approved_by,
            )
            
            # Store key ID reference
            key_info = auth.get_key_info_by_prefix(prefix)
            if key_info:
                agent.api_key_id = key_info["id"]
                self.db.commit()
            
            result["api_key"] = full_key  # Only returned once!
            result["key_prefix"] = prefix
        
        return result
    
    def reject_agent(
        self,
        agent_id: str,
        rejected_by: str = "system",
        reason: Optional[str] = None,
    ) -> bool:
        """Reject a registered agent."""
        agent = self.db.query(RegisteredAgent).filter(
            RegisteredAgent.agent_id == agent_id
        ).first()
        
        if not agent:
            raise AgentRegistryError(f"Agent not found: {agent_id}")
        
        if agent.status == AgentApprovalStatus.APPROVED.value:
            raise AgentRegistryError(f"Agent {agent_id} is already approved, must suspend first")
        
        agent.status = AgentApprovalStatus.REJECTED.value
        agent.rejected_at = datetime.now(timezone.utc).replace(tzinfo=None)
        agent.rejected_by = rejected_by
        agent.rejection_reason = reason
        
        self.db.commit()
        return True
    
    def suspend_agent(
        self,
        agent_id: str,
        suspended_by: str = "system",
        reason: Optional[str] = None,
    ) -> bool:
        """Suspend an approved agent."""
        from src.federation.auth import FederationAuth
        
        agent = self.db.query(RegisteredAgent).filter(
            RegisteredAgent.agent_id == agent_id
        ).first()
        
        if not agent:
            raise AgentRegistryError(f"Agent not found: {agent_id}")
        
        if agent.status != AgentApprovalStatus.APPROVED.value:
            raise AgentRegistryError(f"Agent {agent_id} is not approved (status: {agent.status})")
        
        agent.status = AgentApprovalStatus.SUSPENDED.value
        agent.suspended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        agent.suspended_by = suspended_by
        agent.suspension_reason = reason
        
        # Also revoke their API key
        if agent.api_key_id:
            auth = FederationAuth(self.db)
            auth.revoke_key(agent.api_key_id, revoked_by=suspended_by)
        
        self.db.commit()
        return True
    
    def reactivate_agent(
        self,
        agent_id: str,
        reactivated_by: str = "system",
    ) -> Dict[str, Any]:
        """Reactivate a suspended agent (generate new API key)."""
        from src.federation.auth import FederationAuth
        
        agent = self.db.query(RegisteredAgent).filter(
            RegisteredAgent.agent_id == agent_id
        ).first()
        
        if not agent:
            raise AgentRegistryError(f"Agent not found: {agent_id}")
        
        if agent.status != AgentApprovalStatus.SUSPENDED.value:
            raise AgentRegistryError(f"Agent {agent_id} is not suspended (status: {agent.status})")
        
        agent.status = AgentApprovalStatus.APPROVED.value
        agent.approved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        agent.approved_by = reactivated_by
        agent.suspended_at = None
        agent.suspended_by = None
        agent.suspension_reason = None
        
        self.db.commit()
        self.db.refresh(agent)
        
        # Generate new API key
        auth = FederationAuth(self.db)
        prefix, full_key = auth.create_api_key(
            agent_id=agent_id,
            org_id=agent.org_id,
            name=f"Reactivated API Key for {agent.agent_name}",
            created_by=reactivated_by,
        )
        
        key_info = auth.get_key_info_by_prefix(prefix)
        if key_info:
            agent.api_key_id = key_info["id"]
            self.db.commit()
        
        result = self._to_dict(agent)
        result["api_key"] = full_key
        result["key_prefix"] = prefix
        
        return result
    
    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get agent registration info."""
        agent = self.db.query(RegisteredAgent).filter(
            RegisteredAgent.agent_id == agent_id
        ).first()
        
        if not agent:
            return None
        
        return self._to_dict(agent)
    
    def get_agent_by_org(self, org_id: str) -> List[Dict[str, Any]]:
        """Get all agents for an organization."""
        agents = self.db.query(RegisteredAgent).filter(
            RegisteredAgent.org_id == org_id
        ).all()
        
        return [self._to_dict(a) for a in agents]
    
    def list_agents(
        self,
        status: Optional[AgentApprovalStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List agents with optional status filter."""
        query = self.db.query(RegisteredAgent)
        
        if status:
            query = query.filter(RegisteredAgent.status == status.value)
        
        agents = query.order_by(RegisteredAgent.registered_at.desc()).limit(limit).offset(offset).all()
        
        return [self._to_dict(a) for a in agents]
    
    def list_pending(self, limit: int = 50) -> List[Dict[str, Any]]:
        """List agents pending approval."""
        return self.list_agents(status=AgentApprovalStatus.PENDING, limit=limit)
    
    def update_agent(
        self,
        agent_id: str,
        **updates,
    ) -> Dict[str, Any]:
        """Update agent metadata."""
        agent = self.db.query(RegisteredAgent).filter(
            RegisteredAgent.agent_id == agent_id
        ).first()
        
        if not agent:
            raise AgentRegistryError(f"Agent not found: {agent_id}")
        
        # Allowed updates
        allowed_fields = [
            "agent_name", "agent_version", "org_name", "capabilities",
            "supported_protocols", "contact_email", "webhook_url",
            "description", "website", "logo_url",
        ]
        
        for field, value in updates.items():
            if field in allowed_fields and value is not None:
                setattr(agent, field, value)
        
        agent.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.commit()
        self.db.refresh(agent)
        
        return self._to_dict(agent)
    
    def update_stats(
        self,
        agent_id: str,
        debate_count_delta: int = 0,
        win_delta: int = 0,
        new_avg_score: Optional[float] = None,
    ) -> bool:
        """Update agent debate statistics."""
        agent = self.db.query(RegisteredAgent).filter(
            RegisteredAgent.agent_id == agent_id
        ).first()
        
        if not agent:
            return False
        
        agent.debate_count += debate_count_delta
        agent.win_count += win_delta
        if new_avg_score is not None:
            agent.avg_score = new_avg_score
        agent.last_debate_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        self.db.commit()
        return True
    
    def _to_dict(self, agent: RegisteredAgent) -> Dict[str, Any]:
        """Convert model to dictionary."""
        return {
            "id": agent.id,
            "agent_id": agent.agent_id,
            "agent_name": agent.agent_name,
            "agent_version": agent.agent_version,
            "org_id": agent.org_id,
            "org_name": agent.org_name,
            "capabilities": agent.capabilities,
            "supported_protocols": agent.supported_protocols,
            "contact_email": agent.contact_email,
            "webhook_url": agent.webhook_url,
            "status": agent.status,
            "description": agent.description,
            "website": agent.website,
            "logo_url": agent.logo_url,
            "debate_count": agent.debate_count,
            "win_count": agent.win_count,
            "avg_score": agent.avg_score,
            "registered_at": agent.registered_at.isoformat() if agent.registered_at else None,
            "approved_at": agent.approved_at.isoformat() if agent.approved_at else None,
            "last_debate_at": agent.last_debate_at.isoformat() if agent.last_debate_at else None,
            # Don't include api_key_id for security
        }
