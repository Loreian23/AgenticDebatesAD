"""Federation API endpoints.

These endpoints handle:
- Agent registration and approval
- API key management
- Federation authentication middleware
- External agent join flow
"""

from typing import Optional, Dict, Any
from datetime import datetime
import os
import hmac

from fastapi import APIRouter, HTTPException, Depends, Header, Query, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.database import get_db
from src.federation.auth import FederationAuth, FederationTokenError
from src.federation.agent_registry import AgentRegistry, AgentRegistryError


# ============== Request/Response Models ==============

class AgentRegistrationRequest(BaseModel):
    agent_id: str
    agent_name: str
    org_id: Optional[str] = None
    org_name: Optional[str] = None
    agent_version: Optional[str] = None
    capabilities: Optional[Dict[str, Any]] = None
    supported_protocols: Optional[list] = None
    contact_email: Optional[str] = None
    webhook_url: Optional[str] = None
    description: Optional[str] = None
    website: Optional[str] = None


class AgentRegistrationResponse(BaseModel):
    agent_id: str
    status: str
    message: str


class AgentApprovalRequest(BaseModel):
    approved_by: str = "admin"
    generate_api_key: bool = True


class AgentRejectionRequest(BaseModel):
    rejected_by: str = "admin"
    reason: Optional[str] = None


class AgentSuspensionRequest(BaseModel):
    suspended_by: str = "admin"
    reason: Optional[str] = None


class APIKeyCreateRequest(BaseModel):
    agent_id: str
    org_id: Optional[str] = None
    name: Optional[str] = None
    expires_days: Optional[int] = 90
    rate_limit: int = 60


class APIKeyResponse(BaseModel):
    key_prefix: str
    full_key: str  # Only shown once!
    message: str


class FederationJoinRequest(BaseModel):
    token: str
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None


# ============== Router ==============

router = APIRouter(prefix="/api/federation", tags=["federation"])


# ============== Authentication Dependency ==============

async def require_federation_auth(
    authorization: Optional[str] = Header(None),
    request: Request = None,
):
    """Dependency that requires valid federation API key."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header required")
    
    # Parse Bearer token
    if authorization.startswith("Bearer "):
        api_key = authorization[7:]
    else:
        api_key = authorization
    
    # Get client info
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")
    path = request.url.path
    
    auth = FederationAuth()
    result = auth.validate_request(
        api_key=api_key,
        request_path=path,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    
    if not result.success:
        raise HTTPException(status_code=401, detail=result.error)
    
    return {
        "agent_id": result.agent_id,
        "org_id": result.org_id,
        "key_id": result.key_id,
    }


async def require_admin_auth(
    authorization: Optional[str] = Header(None),
):
    """Dependency for admin-only endpoints (simple shared secret for now)."""
    # TODO: Replace with proper admin auth
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin authorization required")

    expected = os.getenv("ADMIN_API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_API_KEY is not configured")

    admin_token = authorization[7:]
    if not hmac.compare_digest(admin_token, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")

    return {"admin": True}


# ============== Agent Registration Endpoints ==============

@router.post("/agents/register", response_model=AgentRegistrationResponse)
def register_agent(
    req: AgentRegistrationRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Register a new external agent.
    
    Agent will be in PENDING status until admin approves.
    """
    registry = AgentRegistry(db)
    
    try:
        result = registry.register_agent(
            agent_id=req.agent_id,
            agent_name=req.agent_name,
            org_id=req.org_id,
            org_name=req.org_name,
            agent_version=req.agent_version,
            capabilities=req.capabilities,
            supported_protocols=req.supported_protocols,
            contact_email=req.contact_email,
            webhook_url=req.webhook_url,
            description=req.description,
            website=req.website,
            registered_ip=request.client.host if request.client else None,
            registered_user_agent=request.headers.get("User-Agent"),
        )
        
        return AgentRegistrationResponse(
            agent_id=result["agent_id"],
            status=result["status"],
            message="Agent registered successfully. Awaiting admin approval.",
        )
    
    except AgentRegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/agents/{agent_id}/status")
def get_agent_status(
    agent_id: str,
    db: Session = Depends(get_db),
):
    """Get agent approval status."""
    registry = AgentRegistry(db)
    agent = registry.get_agent(agent_id)
    
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    return {
        "agent_id": agent["agent_id"],
        "status": agent["status"],
        "registered_at": agent["registered_at"],
        "approved_at": agent.get("approved_at"),
    }


@router.get("/agents")
def list_agents(
    status: Optional[str] = Query(None),
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """List registered agents (admin only)."""
    from src.federation.agent_registry import AgentApprovalStatus
    
    status_enum = None
    if status:
        try:
            status_enum = AgentApprovalStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid status")
    
    registry = AgentRegistry(db)
    agents = registry.list_agents(status=status_enum, limit=limit, offset=offset)
    
    return {"agents": agents, "count": len(agents)}


@router.get("/agents/pending")
def list_pending_agents(
    limit: int = Query(50, le=100),
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """List agents pending approval (admin only)."""
    registry = AgentRegistry(db)
    agents = registry.list_pending(limit=limit)
    return {"agents": agents, "count": len(agents)}


@router.post("/agents/{agent_id}/approve")
def approve_agent(
    agent_id: str,
    req: AgentApprovalRequest,
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Approve a registered agent (admin only).
    
    Returns API key on success.
    """
    registry = AgentRegistry(db)
    
    try:
        result = registry.approve_agent(
            agent_id=agent_id,
            approved_by=req.approved_by,
            generate_api_key=req.generate_api_key,
        )
        
        response = {
            "agent_id": result["agent_id"],
            "status": result["status"],
            "approved_at": result["approved_at"],
            "message": "Agent approved successfully",
        }
        
        if "api_key" in result:
            response["api_key"] = result["api_key"]
            response["key_prefix"] = result["key_prefix"]
        
        return response
    
    except AgentRegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agents/{agent_id}/reject")
def reject_agent(
    agent_id: str,
    req: AgentRejectionRequest,
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Reject a registered agent (admin only)."""
    registry = AgentRegistry(db)
    
    try:
        registry.reject_agent(
            agent_id=agent_id,
            rejected_by=req.rejected_by,
            reason=req.reason,
        )
        return {"agent_id": agent_id, "status": "rejected", "message": "Agent rejected"}
    
    except AgentRegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agents/{agent_id}/suspend")
def suspend_agent(
    agent_id: str,
    req: AgentSuspensionRequest,
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Suspend an approved agent (admin only)."""
    registry = AgentRegistry(db)
    
    try:
        registry.suspend_agent(
            agent_id=agent_id,
            suspended_by=req.suspended_by,
            reason=req.reason,
        )
        return {"agent_id": agent_id, "status": "suspended", "message": "Agent suspended"}
    
    except AgentRegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agents/{agent_id}/reactivate")
def reactivate_agent(
    agent_id: str,
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Reactivate a suspended agent (admin only).
    
    Returns new API key.
    """
    registry = AgentRegistry(db)
    
    try:
        result = registry.reactivate_agent(agent_id=agent_id)
        
        return {
            "agent_id": result["agent_id"],
            "status": result["status"],
            "api_key": result["api_key"],
            "key_prefix": result["key_prefix"],
            "message": "Agent reactivated",
        }
    
    except AgentRegistryError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============== API Key Management Endpoints ==============

@router.post("/keys", response_model=APIKeyResponse)
def create_api_key(
    req: APIKeyCreateRequest,
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Create an API key for an existing approved agent (admin only).
    
    Returns the full key ONCE - it cannot be retrieved later.
    """
    auth = FederationAuth(db)
    
    prefix, full_key = auth.create_api_key(
        agent_id=req.agent_id,
        org_id=req.org_id,
        name=req.name,
        expires_days=req.expires_days,
        rate_limit=req.rate_limit,
        created_by="admin",
    )
    
    return APIKeyResponse(
        key_prefix=prefix,
        full_key=full_key,
        message="Store this key securely - it will not be shown again!",
    )


@router.get("/keys/{key_id}")
def get_key_info(
    key_id: str,
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Get API key info (admin only)."""
    auth = FederationAuth(db)
    info = auth.get_key_info(key_id)
    
    if not info:
        raise HTTPException(status_code=404, detail="Key not found")
    
    return info


@router.get("/keys/agent/{agent_id}")
def list_keys_for_agent(
    agent_id: str,
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """List all API keys for an agent (admin only)."""
    auth = FederationAuth(db)
    keys = auth.list_keys_for_agent(agent_id)
    return {"keys": keys, "count": len(keys)}


@router.post("/keys/{key_id}/rotate")
def rotate_api_key(
    key_id: str,
    rotated_by: str = "admin",
    grace_hours: int = 24,
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Rotate an API key (admin only).
    
    Old key remains valid during grace period.
    """
    auth = FederationAuth(db)
    
    try:
        prefix, full_key = auth.rotate_key(
            key_id=key_id,
            rotated_by=rotated_by,
            grace_hours=grace_hours,
        )
        
        return {
            "key_id": key_id,
            "key_prefix": prefix,
            "full_key": full_key,
            "message": f"Key rotated. Old key valid for {grace_hours}h grace period.",
        }
    
    except FederationTokenError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/keys/{key_id}/revoke")
def revoke_api_key(
    key_id: str,
    revoked_by: str = "admin",
    _: Dict = Depends(require_admin_auth),
    db: Session = Depends(get_db),
):
    """Revoke an API key immediately (admin only)."""
    auth = FederationAuth(db)
    
    success = auth.revoke_key(key_id=key_id, revoked_by=revoked_by)
    
    if not success:
        raise HTTPException(status_code=404, detail="Key not found")
    
    return {"key_id": key_id, "status": "revoked", "message": "Key revoked immediately"}


# ============== Federation Join Endpoint ==============

@router.post("/debates/join")
def federation_join_debate(
    req: FederationJoinRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Join a debate using a federation invite token.
    
    This is the entry point for external agents to join debates.
    """
    from src.invite_tokens import InviteTokenManager
    from src.models import ParticipantType
    
    # Validate token
    token_manager = InviteTokenManager(db)
    ip_address = request.client.host if request.client else None
    
    is_valid, error, token_record = token_manager.validate_token(req.token, ip_address)
    
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    # Determine agent info
    if req.agent_id:
        registry = AgentRegistry(db)
        agent = registry.get_agent(req.agent_id)
        
        if not agent:
            raise HTTPException(status_code=400, detail="Agent not registered")
        
        if agent["status"] != "approved":
            raise HTTPException(status_code=403, detail=f"Agent status is {agent['status']}")
        
        participant_name = req.agent_name or agent["agent_name"]
    else:
        participant_name = req.agent_name or "External Agent"
    
    # Use the token
    try:
        participant = token_manager.use_token(
            token=req.token,
            participant_name=participant_name,
            ip_address=ip_address,
        )
        
        # Update agent debate count if applicable
        if req.agent_id:
            registry = AgentRegistry(db)
            registry.update_stats(req.agent_id, debate_count_delta=1)
        
        return {
            "participant_id": participant.id,
            "debate_id": participant.debate_id,
            "side": participant.side.value,
            "name": participant.name,
            "message": "Successfully joined debate",
        }
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============== Capabilities Endpoint ==============

@router.get("/capabilities")
def get_capabilities(
    db: Session = Depends(get_db),
):
    """Get platform capabilities and version info."""
    return {
        "platform": "Agent Debate Federation",
        "version": "1.0.0",
        "supported_protocols": ["v1"],
        "debate_formats": [
            "standard",
            "cross_exam",
            "opening_rebuttal_closing",
        ],
        "max_character_limits": {
            "opening": 500,
            "rebuttal": 400,
            "cross_exam_question": 200,
            "cross_exam_answer": 300,
            "closing": 400,
            "team_rebuttal": 800,
        },
        "features": {
            "real_time_updates": True,
            "judge_scoring": True,
            "elo_ratings": True,
            "tournament_brackets": True,
            "export_formats": ["json", "markdown", "csv"],
        },
    }
