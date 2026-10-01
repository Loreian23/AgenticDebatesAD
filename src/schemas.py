"""Pydantic schemas for API validation and serialization."""

import enum
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator, ConfigDict

from src.models import (
    DebateStatus, ParticipantSide, ParticipantType, 
    InviteTokenStatus, AgentMode, AgentPreferredRole, AgentTaskType, AgentTaskStatus
)


# ============== Base Schemas ==============

class DebateBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    proposition: str = Field(..., min_length=10)
    description: Optional[str] = Field(None, max_length=1000)
    max_turn_length: int = Field(default=1000, ge=100, le=5000)
    max_turn_time_seconds: int = Field(default=360, ge=30, le=1800)
    rebuttal_rounds: int = Field(default=2, ge=0, le=5)
    enable_cross_exam: bool = False
    is_public: bool = True


class ParticipantBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    participant_type: ParticipantType = ParticipantType.AGENT
    side: ParticipantSide
    side_order: int = Field(default=0, ge=0)
    agent_provider: Optional[str] = Field(None, max_length=100)


class TurnBase(BaseModel):
    content: str = Field(..., min_length=1)
    replies_to_turn_id: Optional[str] = None


class ScoreBase(BaseModel):
    participant_id: str
    argument_quality: float = Field(..., ge=0, le=10)
    evidence_quality: float = Field(..., ge=0, le=10)
    rebuttal_strength: float = Field(..., ge=0, le=10)
    clarity: float = Field(..., ge=0, le=10)
    compliance: float = Field(..., ge=0, le=10)
    rationale: Optional[str] = Field(None, max_length=2000)
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)


class InviteTokenBase(BaseModel):
    side: ParticipantSide
    participant_type: ParticipantType = ParticipantType.AGENT
    max_uses: int = Field(default=1, ge=1, le=100)
    expires_hours: Optional[int] = Field(default=168, ge=1, le=720)  # 1 week default


# ============== Create Schemas ==============

class DebateCreate(DebateBase):
    created_by: str
    format_mode: str = Field(default="1v1", pattern="^(1v1|2v2)$")
    judges_required: int = Field(default=1, ge=1, le=3)
    content_mode: str = Field(default="simple", pattern="^(simple|rich)$")
    min_turn_ratio: Optional[float] = Field(default=None, ge=0.05, le=0.95)
    judge_time_multiplier: float = Field(default=1.5, ge=1.0, le=4.0)
    initial_participants: Optional[List[ParticipantBase]] = Field(default_factory=list)


class ParticipantCreate(ParticipantBase):
    debate_id: str


class TurnCreate(TurnBase):
    pass


class InviteTokenCreate(InviteTokenBase):
    created_by: str


class ScoreCreate(ScoreBase):
    pass


# ============== Response Schemas ==============

class ParticipantResponse(ParticipantBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    debate_id: str
    joined_at: datetime
    is_active: bool
    turn_count: int = 0


class TurnResponse(TurnBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    debate_id: str
    participant_id: str
    participant_name: str
    participant_side: ParticipantSide
    sequence_number: int
    phase: DebateStatus
    content_length: int
    submitted_at: datetime
    time_taken_seconds: Optional[int]
    was_timeout: bool
    char_limit_violation: bool


class ScoreResponse(ScoreBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    debate_id: str
    judge_id: str
    judge_name: str
    total_score: float
    weighted_score: float
    created_at: datetime
    version: int


class InviteTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    debate_id: str
    token_preview: str
    side: ParticipantSide
    participant_type: ParticipantType
    max_uses: int
    used_count: int
    status: InviteTokenStatus
    expires_at: Optional[datetime]
    created_at: datetime
    # Only include full token on creation
    token: Optional[str] = None


class DebateResponse(DebateBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    status: DebateStatus
    current_phase: DebateStatus
    current_turn_index: int
    created_at: datetime
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    phase_deadline: Optional[datetime]
    winner_side: Optional[ParticipantSide]
    confidence_score: Optional[float]
    judge_rationale: Optional[str]
    created_by: str
    format_mode: str = "1v1"
    judges_required: int = 1
    content_mode: str = "simple"
    min_turn_ratio: float = 0.2
    min_turn_chars: int = 80
    judge_time_multiplier: float = 1.5
    participants: List[ParticipantResponse] = []
    turns: List[TurnResponse] = []
    scores: List[ScoreResponse] = []
    turn_count: int = 0


class DebateListResponse(BaseModel):
    id: str
    title: str
    proposition: str
    status: DebateStatus
    created_at: datetime
    participant_count: int
    turn_count: int
    is_public: bool


class DebateResultsResponse(BaseModel):
    debate: DebateResponse
    team_scores: Dict[str, Any]
    individual_scores: List[Dict[str, Any]]
    winner: Optional[str]
    confidence: float
    rationale: str
    judge_agreement: Optional[Dict[str, Any]] = None
    score_breakdown: Dict[str, Any]


# ============== Update Schemas ==============

class DebateUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    status: Optional[DebateStatus] = None


class TurnSubmit(BaseModel):
    content: str = Field(..., min_length=1)
    
    @field_validator('content')
    @classmethod
    def check_unicode_length(cls, v: str) -> str:
        """Validate actual Unicode character count, not byte length."""
        if not v.strip():
            raise ValueError("Content cannot be empty or whitespace-only")
        # Count Unicode codepoints (actual characters, not bytes)
        char_count = len(v)
        if char_count > 5000:
            raise ValueError(f"Content exceeds maximum character limit (5000)")
        return v


# ============== WebSocket Schemas ==============

class WebSocketMessage(BaseModel):
    type: str  # 'turn_submitted', 'phase_changed', 'participant_joined', etc.
    data: Dict[str, Any]
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TurnOrderItem(BaseModel):
    participant_id: str
    participant_name: str
    side: ParticipantSide
    sequence_number: int
    phase: DebateStatus
    status: str  # 'pending', 'current', 'completed'


class DebateStateUpdate(BaseModel):
    debate_id: str
    status: DebateStatus
    current_phase: DebateStatus
    current_turn: Optional[TurnOrderItem]
    turn_order: List[TurnOrderItem]
    phase_deadline: Optional[datetime]
    recent_turns: List[TurnResponse]


# ============== Join Schemas ==============

class JoinDebateRequest(BaseModel):
    token: str
    name: str = Field(..., min_length=1, max_length=100)
    participant_type: Optional[ParticipantType] = None


class JoinDebateResponse(BaseModel):
    success: bool
    participant_id: Optional[str] = None
    debate_id: Optional[str] = None
    session_token: Optional[str] = None
    error: Optional[str] = None


# ============== Agent Polling Schemas ==============

class WaitForTurnResponse(BaseModel):
    your_turn: bool
    phase: DebateStatus
    debate_status: DebateStatus
    current_turn_participant_id: Optional[str] = None
    current_turn_index: int = 0
    phase_deadline: Optional[datetime] = None
    recommended_poll_after_seconds: int = 5
    turn_sequence_number: Optional[int] = None
    last_turn_submitted_at: Optional[datetime] = None


class DebateHealthResponse(BaseModel):
    debate_id: str
    status: str
    current_phase: str
    is_stalled: bool
    stalled_since: Optional[str] = None
    turns_in_current_phase: int = 0
    current_turn_index: int
    last_turn_at: Optional[datetime] = None
    participants_active: int


class HeartbeatRequest(BaseModel):
    participant_id: str


class HeartbeatResponse(BaseModel):
    ok: bool
    debate_status: DebateStatus
    phase: DebateStatus
    is_turn: bool
    recommended_poll_after_seconds: int = 5


class ParticipantReconnectRequest(BaseModel):
    session_token: str


class ParticipantReconnectResponse(BaseModel):
    ok: bool
    participant_id: str
    debate_id: str
    session_token: str
    error: Optional[str] = None


class AgentJoinRequest(BaseModel):
    debate_id: Optional[str] = None
    invite_token: Optional[str] = Field(None, min_length=8, max_length=128)
    agent_name: str = Field(..., min_length=1, max_length=100)
    model: Optional[str] = Field(None, max_length=120)
    preferred_role: AgentPreferredRole = AgentPreferredRole.AUTO
    mode: AgentMode = AgentMode.AUTO


class AgentJoinResponse(BaseModel):
    ok: bool
    token: str
    token_expires_at: datetime
    session_id: str
    debate_id: str
    participant_id: str
    assigned_role: ParticipantSide
    poll_endpoint: str
    submit_endpoint_template: str
    heartbeat_endpoint_template: str
    reconnected: Optional[bool] = None


class TaskCompleteTurnPayload(BaseModel):
    content: str = Field(..., min_length=1)

    @field_validator('content')
    @classmethod
    def check_non_whitespace_content(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Content cannot be empty or whitespace-only")
        return v


class TaskCompleteJudgePayload(BaseModel):
    argument_quality: float = Field(..., ge=0, le=10)
    evidence_quality: float = Field(..., ge=0, le=10)
    rebuttal_strength: float = Field(..., ge=0, le=10)
    clarity: float = Field(..., ge=0, le=10)
    compliance: float = Field(..., ge=0, le=10)
    rationale: Optional[str] = Field(None, max_length=2000)
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)


class AgentTaskView(BaseModel):
    id: str
    debate_id: str
    task_type: AgentTaskType
    phase: DebateStatus
    status: AgentTaskStatus
    lease_expires_at: Optional[datetime]
    payload: Dict[str, Any]


class AgentTaskNextResponse(BaseModel):
    ok: bool
    debate_status: DebateStatus
    task: Optional[AgentTaskView] = None
    in_flight_task: Optional[AgentTaskView] = None
    lease_warning: bool = False
    wait_ms: int = 1000
    wait_context: Optional[Dict[str, Any]] = None
    terminal: bool = False
    terminal_reason: Optional[str] = None
    must_continue_polling: bool = True
    stop_only_if_terminal: bool = True
    token_expires_at: Optional[datetime] = None


class AgentTaskCompleteRequest(BaseModel):
    idempotency_key: Optional[str] = Field(None, max_length=120)
    turn: Optional[TaskCompleteTurnPayload] = None
    judge_score: Optional[TaskCompleteJudgePayload] = None


class AgentTaskCompleteResponse(BaseModel):
    ok: bool
    task_id: str
    status: AgentTaskStatus
    idempotent_replay: bool = False


# ============== Export Schemas ==============

class ExportFormat(str, enum.Enum):
    JSON = "json"
    MARKDOWN = "markdown"
    CSV = "csv"
    PDF = "pdf"


class DebateExportRequest(BaseModel):
    format: ExportFormat = ExportFormat.MARKDOWN
    include_scores: bool = True
    include_turns: bool = True


class DebateExportResponse(BaseModel):
    content: str
    filename: str
    content_type: str
