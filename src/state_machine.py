"""Debate state machine with strict turn management."""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Tuple, Any
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

from src.models import (
    Debate, DebateStatus, Participant, ParticipantSide, 
    Turn, Score, AuditLog, AgentSession, AgentTaskType
)
from src.database import get_db_session

# Grace extensions granted when no judge has scored by the judging deadline
# before the debate is cancelled. Each extension = one full judging window,
# giving a slow or restarted judge agent more time to submit scores.
JUDGING_GRACE_MAX_EXTENSIONS = 3


class StateMachineError(Exception):
    """Error in state machine operation."""
    pass


class InvalidTurnError(StateMachineError):
    """Invalid turn submission."""
    pass


class StateTransitionError(StateMachineError):
    """Invalid state transition."""
    pass


class DebateStateMachine:
    """
    Manages debate state transitions and turn order.
    
    State flow:
    PENDING → OPENING → REBUTTAL_1 → [REBUTTAL_2] → [CROSS_EXAM] → CLOSING → JUDGING → COMPLETE
    """
    
    # Valid state transitions
    VALID_TRANSITIONS = {
        DebateStatus.PENDING: [DebateStatus.OPENING, DebateStatus.CANCELLED],
        DebateStatus.OPENING: [DebateStatus.REBUTTAL_1, DebateStatus.CANCELLED],
        DebateStatus.REBUTTAL_1: [DebateStatus.REBUTTAL_2, DebateStatus.CROSS_EXAM, DebateStatus.CLOSING, DebateStatus.CANCELLED],
        DebateStatus.REBUTTAL_2: [DebateStatus.CROSS_EXAM, DebateStatus.CLOSING, DebateStatus.CANCELLED],
        DebateStatus.CROSS_EXAM: [DebateStatus.CLOSING, DebateStatus.CANCELLED],
        DebateStatus.CLOSING: [DebateStatus.JUDGING, DebateStatus.CANCELLED],
        DebateStatus.JUDGING: [DebateStatus.COMPLETE, DebateStatus.CANCELLED],
        DebateStatus.COMPLETE: [],
        DebateStatus.CANCELLED: [],
    }
    
    # Fallback phase durations (seconds). Runtime uses debate.max_turn_time_seconds
    # for speech/judging phases to keep timing uniform across rounds.
    DEFAULT_PHASE_DURATIONS = {
        DebateStatus.OPENING: 360,
        DebateStatus.REBUTTAL_1: 360,
        DebateStatus.REBUTTAL_2: 360,
        DebateStatus.CROSS_EXAM: 360,
        DebateStatus.CLOSING: 360,
        DebateStatus.JUDGING: 360,
    }
    
    def __init__(self, debate_id: str, db: Optional[Session] = None):
        self.debate_id = debate_id
        self.db = db or get_db_session()
        self._debate: Optional[Debate] = None
        self._turn_order: List[Dict[str, Any]] = []
        self._current_turn_index: int = 0
    
    def _get_debate(self) -> Debate:
        """Get debate with fresh data."""
        if self._debate is None:
            self._debate = self.db.query(Debate).filter(Debate.id == self.debate_id).first()
            if not self._debate:
                raise StateMachineError(f"Debate {self.debate_id} not found")
        return self._debate

    def _required_team_size(self) -> int:
        debate = self._get_debate()
        meta = debate.metadata_json or {}
        try:
            value = int(meta.get("team_size_per_side", 1))
        except (ValueError, TypeError):
            value = 1
        return max(1, value)

    def _required_judges(self) -> int:
        debate = self._get_debate()
        meta = debate.metadata_json or {}
        try:
            value = int(meta.get("judges_required", 1))
        except (ValueError, TypeError):
            value = 1
        return max(1, value)

    def _phase_duration_seconds(self, phase: DebateStatus) -> int:
        debate = self._get_debate()
        uniform = int(debate.max_turn_time_seconds or 360)
        # Per-agent fairness: each turn gets its own deadline, independent of
        # how many agents share the phase. This ensures slow agents don't steal
        # time from faster ones in the same phase.
        if phase in {
            DebateStatus.OPENING,
            DebateStatus.REBUTTAL_1,
            DebateStatus.REBUTTAL_2,
            DebateStatus.CROSS_EXAM,
            DebateStatus.CLOSING,
        }:
            return max(30, uniform)

        if phase == DebateStatus.JUDGING:
            pro_count = len(self.get_participants_by_side(ParticipantSide.PRO))
            con_count = len(self.get_participants_by_side(ParticipantSide.CON))
            judge_count = len(self.get_participants_by_side(ParticipantSide.JUDGE))
            score_tasks = max(1, (pro_count + con_count) * max(1, judge_count))
            meta = debate.metadata_json or {}
            try:
                judge_multiplier = float(meta.get("judge_time_multiplier", 1.5))
            except (ValueError, TypeError):
                judge_multiplier = 1.5
            judge_multiplier = min(4.0, max(1.0, judge_multiplier))
            per_score_seconds = int(max(30, uniform) * judge_multiplier)
            return per_score_seconds * score_tasks

        return self.DEFAULT_PHASE_DURATIONS.get(phase, 360)
    
    def _audit_log(self, event_type: str, event_data: dict, actor_type: str = "system", actor_id: Optional[str] = None):
        """Create audit log entry."""
        log = AuditLog(
            debate_id=self.debate_id,
            event_type=event_type,
            event_data=event_data,
            actor_type=actor_type,
            actor_id=actor_id,
        )
        self.db.add(log)
        self.db.commit()
    
    def get_participants_by_side(self, side: ParticipantSide) -> List[Participant]:
        """Get active participants on a given side."""
        debate = self._get_debate()
        return [
            p for p in debate.participants 
            if p.side == side and p.is_active
        ]
    
    def build_turn_order(self) -> List[Dict[str, Any]]:
        """
        Build the complete turn order for the debate.
        
        Pattern: Alternating sides, respecting side_order within each side.
        """
        debate = self._get_debate()
        
        # Get active debaters (not judges/observers)
        pro_debaters = sorted(
            [p for p in debate.participants if p.side == ParticipantSide.PRO and p.is_active],
            key=lambda p: p.side_order
        )
        con_debaters = sorted(
            [p for p in debate.participants if p.side == ParticipantSide.CON and p.is_active],
            key=lambda p: p.side_order
        )
        
        turn_order = []
        sequence = 0
        
        # OPENING: All debaters give opening statements
        # Pro goes first, then Con (alternating by side_order)
        max_opening = max(len(pro_debaters), len(con_debaters))
        for i in range(max_opening):
            if i < len(pro_debaters):
                sequence += 1
                turn_order.append({
                    "sequence_number": sequence,
                    "phase": DebateStatus.OPENING,
                    "participant_id": pro_debaters[i].id,
                    "side": ParticipantSide.PRO,
                    "status": "pending"
                })
            if i < len(con_debaters):
                sequence += 1
                turn_order.append({
                    "sequence_number": sequence,
                    "phase": DebateStatus.OPENING,
                    "participant_id": con_debaters[i].id,
                    "side": ParticipantSide.CON,
                    "status": "pending"
                })
        
        # REBUTTALS: Alternating sides, each debater responds
        for round_num in range(1, debate.rebuttal_rounds + 1):
            phase = DebateStatus.REBUTTAL_1 if round_num == 1 else DebateStatus.REBUTTAL_2
            # Alternate which side starts each round
            sides_in_order = [ParticipantSide.CON, ParticipantSide.PRO] if round_num % 2 == 1 else [ParticipantSide.PRO, ParticipantSide.CON]
            
            for side in sides_in_order:
                debaters = pro_debaters if side == ParticipantSide.PRO else con_debaters
                for debater in debaters:
                    sequence += 1
                    turn_order.append({
                        "sequence_number": sequence,
                        "phase": phase,
                        "participant_id": debater.id,
                        "side": side,
                        "status": "pending"
                    })
        
        # CROSS EXAMINATION (optional)
        if debate.enable_cross_exam:
            for side in [ParticipantSide.PRO, ParticipantSide.CON]:
                debaters = pro_debaters if side == ParticipantSide.PRO else con_debaters
                for debater in debaters:
                    sequence += 1
                    turn_order.append({
                        "sequence_number": sequence,
                        "phase": DebateStatus.CROSS_EXAM,
                        "participant_id": debater.id,
                        "side": side,
                        "status": "pending"
                    })
        
        # CLOSING: Final statements
        for side in [ParticipantSide.PRO, ParticipantSide.CON]:
            debaters = pro_debaters if side == ParticipantSide.PRO else con_debaters
            for debater in debaters:
                sequence += 1
                turn_order.append({
                    "sequence_number": sequence,
                    "phase": DebateStatus.CLOSING,
                    "participant_id": debater.id,
                    "side": side,
                    "status": "pending"
                })
        
        # Rehydrate submitted turns from DB so order remains stable across page refreshes.
        completed_keys = set()
        for t in debate.turns:
            phase_value = t.phase.value if hasattr(t.phase, "value") else str(t.phase)
            completed_keys.add((t.sequence_number, phase_value))

        for turn in turn_order:
            turn_phase_value = turn["phase"].value if hasattr(turn["phase"], "value") else str(turn["phase"])
            if (turn["sequence_number"], turn_phase_value) in completed_keys:
                turn["status"] = "completed"

        self._turn_order = turn_order
        return turn_order
    
    def get_current_turn(self) -> Optional[Dict[str, Any]]:
        """Get the current turn that needs to be taken."""
        debate = self._get_debate()
        
        if not self._turn_order:
            self.build_turn_order()
        
        # Find first pending turn in current phase
        for turn in self._turn_order:
            if turn["phase"] == debate.current_phase and turn["status"] == "pending":
                return turn
        
        return None
    
    def can_submit_turn(self, participant_id: str) -> Tuple[bool, Optional[str]]:
        """Check if participant can submit a turn right now."""
        debate = self._get_debate()
        
        # Check debate status
        if debate.status not in [
            DebateStatus.OPENING, DebateStatus.REBUTTAL_1, 
            DebateStatus.REBUTTAL_2, DebateStatus.CROSS_EXAM, 
            DebateStatus.CLOSING
        ]:
            return False, f"Debate is in {debate.status.value} phase, cannot submit turns"
        
        # Get current turn
        current = self.get_current_turn()
        if not current:
            return False, "No pending turns in current phase"
        
        # Check if it's this participant's turn
        if current["participant_id"] != participant_id:
            return False, f"Not your turn. Current turn is for participant {current['participant_id']}"
        
        # Check phase deadline
        if debate.phase_deadline and datetime.now(timezone.utc).replace(tzinfo=None) > debate.phase_deadline:
            return False, "Phase deadline has passed"
        
        return True, None
    
    def submit_turn(self, participant_id: str, content: str, 
                    time_taken_seconds: Optional[int] = None) -> Turn:
        """
        Submit a turn for the current participant.
        
        Raises InvalidTurnError if validation fails.
        """
        debate = self._get_debate()
        
        # Validate turn can be submitted
        can_submit, error = self.can_submit_turn(participant_id)
        if not can_submit:
            raise InvalidTurnError(error)
        
        # Get current turn info
        current = self.get_current_turn()

        # Reject empty/whitespace-only turns
        if not content or not content.strip():
            raise InvalidTurnError("Turn content cannot be empty or whitespace-only")
        
        # Validate content length (Unicode characters, not bytes)
        char_count = len(content)
        char_limit = debate.max_turn_length
        char_violation = char_count > char_limit
        if char_violation:
            raise InvalidTurnError(
                f"Content exceeds maximum length ({char_count} > {char_limit} characters)"
            )
        
        # Calculate time taken
        if time_taken_seconds is None and debate.started_at:
            time_taken_seconds = int((datetime.now(timezone.utc).replace(tzinfo=None) - debate.started_at).total_seconds())
        
        # Create turn
        turn = Turn(
            debate_id=self.debate_id,
            participant_id=participant_id,
            sequence_number=current["sequence_number"],
            phase=debate.current_phase,
            content=content,
            content_length=char_count,
            time_taken_seconds=time_taken_seconds,
            char_limit_violation=char_violation,
        )
        
        self.db.add(turn)
        
        # Update turn order status
        for t in self._turn_order:
            if t["sequence_number"] == current["sequence_number"]:
                t["status"] = "completed"
                break
        
        # Audit log
        self._audit_log(
            "turn_submitted",
            {
                "participant_id": participant_id,
                "sequence_number": current["sequence_number"],
                "phase": debate.current_phase.value,
                "char_count": char_count,
                "char_limit_violation": char_violation,
            },
            actor_type="agent",
            actor_id=participant_id
        )
        
        self.db.commit()
        self.db.refresh(turn)
        
        # Check if phase is complete
        phase_advanced = self._check_phase_completion()
        
        # If phase didn't advance, there are more turns in this phase.
        # Reset the deadline so the NEXT agent gets a full turn window
        # (per-agent fairness, not shared-phase deadline).
        if not phase_advanced:
            debate = self._get_debate()
            debate.phase_deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=debate.max_turn_time_seconds)
            self.db.commit()
        
        return turn
    
    def _check_phase_completion(self) -> bool:
        """Check if current phase is complete and advance if needed."""
        debate = self._get_debate()
        
        # Count pending turns in current phase
        pending_in_phase = sum(
            1 for t in self._turn_order 
            if t["phase"] == debate.current_phase and t["status"] == "pending"
        )
        
        if pending_in_phase == 0:
            # Phase is complete, advance
            self._advance_phase()
            return True
        
        return False
    
    def _advance_phase(self):
        """Advance to the next phase of the debate."""
        debate = self._get_debate()
        current = debate.current_phase
        
        # Determine next phase
        transitions = self.VALID_TRANSITIONS.get(current, [])
        
        if current == DebateStatus.OPENING:
            next_phase = DebateStatus.REBUTTAL_1
        elif current == DebateStatus.REBUTTAL_1 and debate.rebuttal_rounds >= 2:
            next_phase = DebateStatus.REBUTTAL_2
        elif current == DebateStatus.REBUTTAL_2 and debate.enable_cross_exam:
            next_phase = DebateStatus.CROSS_EXAM
        elif current in [DebateStatus.REBUTTAL_1, DebateStatus.REBUTTAL_2, DebateStatus.CROSS_EXAM]:
            next_phase = DebateStatus.CLOSING
        elif current == DebateStatus.CLOSING:
            next_phase = DebateStatus.JUDGING
        elif current == DebateStatus.JUDGING:
            next_phase = DebateStatus.COMPLETE
            debate.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            raise StateTransitionError(f"Cannot advance from phase {current}")
        
        # Validate transition
        if next_phase not in transitions:
            raise StateTransitionError(f"Invalid transition from {current} to {next_phase}")
        
        # Update debate
        old_phase = debate.current_phase
        debate.current_phase = next_phase
        debate.status = next_phase
        
        # Set phase deadline
        duration = self._phase_duration_seconds(next_phase)
        debate.phase_deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=duration)
        
        # Audit log
        self._audit_log(
            "phase_advanced",
            {
                "from_phase": old_phase.value,
                "to_phase": next_phase.value,
                "deadline": debate.phase_deadline.isoformat() if debate.phase_deadline else None,
            }
        )
        
        self.db.commit()
    
    def start_debate(self) -> Debate:
        """Start the debate from PENDING state."""
        debate = self._get_debate()
        
        if debate.status != DebateStatus.PENDING:
            raise StateTransitionError(f"Cannot start debate from {debate.status}")
        
        # Build turn order
        self.build_turn_order()
        
        # Validate minimum roster requirements. The judge is intentionally NOT
        # required here — it can join anytime before the JUDGING phase, so the
        # debate starts as soon as both debating sides are filled (faster onboarding).
        pro_count = len(self.get_participants_by_side(ParticipantSide.PRO))
        con_count = len(self.get_participants_by_side(ParticipantSide.CON))
        
        required_per_side = self._required_team_size()

        if pro_count < required_per_side:
            raise StateTransitionError(f"Need at least {required_per_side} PRO participant(s)")
        if con_count < required_per_side:
            raise StateTransitionError(f"Need at least {required_per_side} CON participant(s)")
        
        # Advance to opening
        debate.status = DebateStatus.OPENING
        debate.current_phase = DebateStatus.OPENING
        debate.started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        debate.phase_deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=self._phase_duration_seconds(DebateStatus.OPENING))
        
        self._audit_log("debate_started", {"participant_count": pro_count + con_count})
        
        self.db.commit()
        self.db.refresh(debate)
        return debate
    
    def cancel_debate(self, reason: Optional[str] = None) -> Debate:
        """Cancel the debate."""
        debate = self._get_debate()
        
        if debate.status in [DebateStatus.COMPLETE, DebateStatus.CANCELLED]:
            raise StateTransitionError(f"Cannot cancel debate in {debate.status}")
        
        debate.status = DebateStatus.CANCELLED
        debate.current_phase = DebateStatus.CANCELLED
        debate.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        self._audit_log("debate_cancelled", {"reason": reason})
        
        self.db.commit()
        self.db.refresh(debate)
        return debate
    
    def get_debate_state(self) -> Dict[str, Any]:
        """Get current debate state for realtime updates."""
        # Always rebuild from persisted DB state so websocket refreshes and page reloads
        # cannot drift from in-memory turn order.
        self._debate = None
        self._turn_order = []

        debate = self._get_debate()
        self.build_turn_order()
        current_turn = self.get_current_turn()

        # Get participant info for turn order
        participant_map = {p.id: p for p in debate.participants}

        turn_order_display = []
        for t in self._turn_order:
            p = participant_map.get(t["participant_id"])
            turn_order_display.append({
                "participant_id": t["participant_id"],
                "participant_name": p.name if p else "Unknown",
                "side": t["side"].value,
                "sequence_number": t["sequence_number"],
                "phase": t["phase"].value,
                "status": t["status"]
            })

        # Get recent turns
        recent_turns = [
            {
                "id": t.id,
                "participant_id": t.participant_id,
                "participant_name": participant_map.get(t.participant_id, Participant(name="Unknown")).name,
                "side": participant_map.get(t.participant_id, Participant(side=ParticipantSide.OBSERVER)).side.value,
                "sequence_number": t.sequence_number,
                "phase": t.phase.value,
                "content": t.content,
                "content_preview": t.content,
                "submitted_at": t.submitted_at.isoformat() if t.submitted_at else None,
            }
            for t in debate.turns[-10:]  # Last 10 turns
        ]

        return {
            "debate_id": debate.id,
            "status": debate.status.value,
            "current_phase": debate.current_phase.value,
            "current_turn": {
                "participant_id": current_turn["participant_id"],
                "participant_name": participant_map.get(current_turn["participant_id"], Participant(name="Unknown")).name,
                "side": current_turn["side"].value,
                "sequence_number": current_turn["sequence_number"],
                "phase": current_turn["phase"].value,
            } if current_turn else None,
            "turn_order": turn_order_display,
            "phase_deadline": debate.phase_deadline.isoformat() if debate.phase_deadline else None,
            "recent_turns": recent_turns,
        }


class TurnTimeoutHandler:
    """Handle turn timeouts and auto-advance with retry logic."""
    
    MAX_RETRIES = 3
    RETRY_DELAY_BASE = 0.1  # Base delay in seconds
    FIRST_TIMEOUT_GRACE_SECONDS = 180  # Grace period per extension
    MAX_GRACE_EXTENSIONS = 5  # After this many grace extensions, force timeout (15 min total)
    MAX_PHASE_DURATION_SECONDS = 3600  # Hard cap: 1 hour per phase before forced advance
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or get_db_session()
    
    def _process_single_timeout_with_retry(self, debate: Debate) -> Optional[Dict[str, Any]]:
        """Process timeout for a single debate with retry on DB lock."""
        last_error = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                return self._process_single_timeout(debate)
            except Exception as e:
                last_error = e
                if "database is locked" in str(e).lower() or "lock" in str(e).lower():
                    import time
                    delay = self.RETRY_DELAY_BASE * (2 ** attempt)  # Exponential backoff
                    time.sleep(delay)
                    # Refresh the db session
                    self.db.rollback()
                    continue
                else:
                    raise
        
        # All retries exhausted
        logging.getLogger(__name__).warning(f"Failed to process timeout for debate {debate.id} after {self.MAX_RETRIES} attempts: {last_error}")
        return None
    
    def _process_single_timeout(self, debate: Debate) -> Optional[Dict[str, Any]]:
        """Process timeout for a single debate.
        
        Implements a two-stage timeout:
        1. First timeout → extend deadline by grace period, re-materialize task
        2. Second timeout → submit placeholder (agent truly unresponsive)
        """
        sm = DebateStateMachine(debate.id, self.db)
        
        current = sm.get_current_turn()
        if not current:
            return None
        
        participant = self.db.query(Participant).filter(
            Participant.id == current["participant_id"]
        ).first()
        
        # Check if this is the first timeout for this phase/turn
        meta = debate.metadata_json or {}
        timeout_grace_count = meta.get("_timeout_grace_count", 0)
        
        if timeout_grace_count < self.MAX_GRACE_EXTENSIONS:
            # GRACE EXTENSION — try to recover (up to MAX_GRACE_EXTENSIONS times)
            extension_number = timeout_grace_count + 1
            
            # Check if any active session exists for this participant
            active_sessions = self.db.query(AgentSession).filter(
                AgentSession.debate_id == debate.id,
                AgentSession.participant_id == current["participant_id"],
                AgentSession.is_active == True,
            ).count()
            
            # Re-materialize: create a new PENDING task if none exists.
            # AgentTask has required routing/dedupe fields, so rebuild the same
            # minimal task shape used by the live worker queue instead of
            # inserting a partial row during timeout recovery.
            from src.models import AgentTask, AgentTaskStatus
            existing_task = self.db.query(AgentTask).filter(
                AgentTask.debate_id == debate.id,
                AgentTask.participant_id == current["participant_id"],
                AgentTask.status.in_([AgentTaskStatus.PENDING, AgentTaskStatus.LEASED]),
            ).first()
            
            if not existing_task:
                phase_value = current["phase"].value if hasattr(current["phase"], "value") else str(current["phase"])
                task = AgentTask(
                    debate_id=debate.id,
                    participant_id=current["participant_id"],
                    assigned_side=current["side"],
                    phase=current["phase"],
                    task_type=AgentTaskType.TURN,
                    status=AgentTaskStatus.PENDING,
                    dedupe_key=f"{debate.id}:{current['participant_id']}:{phase_value}:{current['sequence_number']}:timeout-recovery",
                    payload_json={
                        "debate_id": debate.id,
                        "participant_id": current["participant_id"],
                        "assigned_role": current["side"].value if hasattr(current["side"], "value") else str(current["side"]),
                        "phase": phase_value,
                        "sequence_number": current["sequence_number"],
                        "timeout_recovery": True,
                    },
                    created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    available_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                self.db.add(task)
            elif existing_task.status == AgentTaskStatus.LEASED:
                # Expire stale lease so task becomes available
                existing_task.leased_by_session_id = None
                existing_task.lease_expires_at = None
                existing_task.status = AgentTaskStatus.PENDING
            
            # Extend deadline by grace period
            grace_seconds = self.FIRST_TIMEOUT_GRACE_SECONDS
            debate.phase_deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=grace_seconds)
            meta["_timeout_grace_count"] = extension_number
            debate.metadata_json = meta
            
            self.db.add(AuditLog(
                debate_id=debate.id,
                event_type="turn_timeout_grace_extended",
                event_data={
                    "participant_id": current["participant_id"],
                    "participant_name": participant.name if participant else None,
                    "participant_side": participant.side.value if participant and participant.side else None,
                    "phase": debate.current_phase.value if debate.current_phase else None,
                    "sequence_number": current["sequence_number"],
                    "grace_seconds": grace_seconds,
                    "active_sessions": active_sessions,
                    "task_recreated": not bool(existing_task),
                    "extension_number": extension_number,
                    "max_extensions": self.MAX_GRACE_EXTENSIONS,
                    "forcing_timeout": extension_number >= self.MAX_GRACE_EXTENSIONS,
                },
                actor_type="system",
                actor_id="timeout-sweeper",
            ))
            self.db.commit()
            
            return {
                "debate_id": debate.id,
                "participant_id": current["participant_id"],
                "sequence_number": current["sequence_number"],
                "action": "grace_extended",
                "grace_seconds": grace_seconds,
                "extension_number": extension_number,
                "forcing_timeout": extension_number >= self.MAX_GRACE_EXTENSIONS,
            }
        
        # SECOND TIMEOUT — grace exhausted, submit placeholder
        # Create timeout turn entry
        timeout_turn = Turn(
            debate_id=debate.id,
            participant_id=current["participant_id"],
            sequence_number=current["sequence_number"],
            phase=debate.current_phase,
            content="[TIMEOUT - No response within time limit]",
            content_length=0,
            was_timeout=True,
        )
        self.db.add(timeout_turn)

        latest_session = self.db.query(AgentSession).filter(
            AgentSession.debate_id == debate.id,
            AgentSession.participant_id == current["participant_id"],
            AgentSession.is_active == True,
        ).order_by(AgentSession.last_seen_at.desc()).first()
        model_name = None
        if latest_session and latest_session.model_name:
            model_name = latest_session.model_name
        elif participant and participant.agent_provider:
            model_name = participant.agent_provider

        self.db.add(AuditLog(
            debate_id=debate.id,
            event_type="agent_turn_timeout",
            event_data={
                "participant_id": current["participant_id"],
                "participant_name": participant.name if participant else None,
                "participant_side": participant.side.value if participant and participant.side else None,
                "model_name": model_name,
                "phase": debate.current_phase.value if debate.current_phase else None,
                "sequence_number": current["sequence_number"],
                "phase_deadline": debate.phase_deadline.isoformat() if debate.phase_deadline else None,
                "max_turn_time_seconds": debate.max_turn_time_seconds,
                "reason": "no_response_within_time_limit_after_grace",
            },
            actor_type="agent" if participant else "system",
            actor_id=current["participant_id"],
        ))
        
        # Reset grace counter for next turn in this phase
        meta["_timeout_grace_count"] = 0
        debate.metadata_json = meta
        
        # Mark as completed in turn order
        for t in sm._turn_order:
            if t["sequence_number"] == current["sequence_number"]:
                t["status"] = "completed"
                break
        
        results = {
            "debate_id": debate.id,
            "participant_id": current["participant_id"],
            "sequence_number": current["sequence_number"],
            "model_name": model_name,
        }
        
        # Check if phase can advance
        sm._check_phase_completion()
        
        # If still in same speech phase after timeout, set per-turn
        # deadline for the next agent (per-agent fairness).
        debate_refreshed = self.db.query(Debate).filter(Debate.id == debate.id).first()
        if debate_refreshed and debate_refreshed.status in [
            DebateStatus.OPENING, DebateStatus.REBUTTAL_1,
            DebateStatus.REBUTTAL_2, DebateStatus.CROSS_EXAM,
            DebateStatus.CLOSING
        ]:
            debate_refreshed.phase_deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=debate_refreshed.max_turn_time_seconds)
            self.db.commit()
        
        return results
    
    def process_timeouts(self) -> List[Dict[str, Any]]:
        """Process all timed-out debates and return affected debates."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Find debates with expired phase deadlines (all active phases including judging)
        expired_debates = self.db.query(Debate).filter(
            and_(
                Debate.phase_deadline < now,
                Debate.status.in_([
                    DebateStatus.OPENING, DebateStatus.REBUTTAL_1,
                    DebateStatus.REBUTTAL_2, DebateStatus.CROSS_EXAM,
                    DebateStatus.CLOSING, DebateStatus.JUDGING
                ])
            )
        ).all()
        
        results = []
        for debate in expired_debates:
            if debate.status == DebateStatus.JUDGING:
                result = self._process_judging_timeout(debate)
            else:
                result = self._process_single_timeout_with_retry(debate)
            if result:
                results.append(result)
        
        self.db.commit()
        return results

    def _process_judging_timeout(self, debate: Debate) -> Optional[Dict[str, Any]]:
        """Handle judging phase timeout: no judge submitted in time."""
        from src.models import Score
        expected_judges = self.db.query(Participant).filter(
            Participant.debate_id == debate.id,
            Participant.side == ParticipantSide.JUDGE,
            Participant.is_active == True,
        ).count()
        expected_debaters = self.db.query(Participant).filter(
            Participant.debate_id == debate.id,
            Participant.side.in_([ParticipantSide.PRO, ParticipantSide.CON]),
            Participant.is_active == True,
        ).count()
        expected_scores = expected_judges * expected_debaters
        actual_scores = self.db.query(Score).filter(Score.debate_id == debate.id).count()

        if actual_scores == 0:
            # No judge has scored yet. Grant a grace extension (the judge task is
            # re-emitted naturally on the judge's next /api/tasks/next poll) instead
            # of cancelling immediately — a slow or restarted judge gets more time.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            meta = dict(debate.metadata_json or {})
            grace_used = int(meta.get("judging_grace_extensions", 0) or 0)
            has_active_judge = expected_judges > 0

            if has_active_judge and grace_used < JUDGING_GRACE_MAX_EXTENSIONS:
                grace_seconds = max(60, DebateStateMachine(debate.id, self.db)._phase_duration_seconds(DebateStatus.JUDGING))
                debate.phase_deadline = now + timedelta(seconds=grace_seconds)
                meta["judging_grace_extensions"] = grace_used + 1
                debate.metadata_json = meta
                self.db.add(AuditLog(
                    debate_id=debate.id,
                    event_type="judging_grace_extended",
                    event_data={
                        "reason": "no_judge_scores_submitted",
                        "grace_extension": grace_used + 1,
                        "max_extensions": JUDGING_GRACE_MAX_EXTENSIONS,
                        "expected_scores": expected_scores,
                        "actual_scores": actual_scores,
                        "new_deadline": debate.phase_deadline.isoformat(),
                    },
                    actor_type="system",
                ))
                return {"debate_id": debate.id, "action": "grace_extended", "reason": "judging_timeout_zero_scores"}

            # No active judge, or grace budget exhausted — cancel the debate.
            debate.status = DebateStatus.CANCELLED
            debate.current_phase = DebateStatus.CANCELLED
            debate.ended_at = now
            self.db.add(AuditLog(
                debate_id=debate.id,
                event_type="judging_timeout_cancelled",
                event_data={
                    "reason": "no_judge_scores_submitted",
                    "expected_scores": expected_scores,
                    "actual_scores": actual_scores,
                    "grace_extensions_used": grace_used,
                },
                actor_type="system",
            ))
            return {"debate_id": debate.id, "action": "cancelled", "reason": "judging_timeout_zero_scores"}
        elif actual_scores < expected_scores:
            # Partial scores — mark complete with note
            debate.status = DebateStatus.COMPLETE
            debate.current_phase = DebateStatus.COMPLETE
            debate.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
            debate.judge_rationale = f"Judging incomplete: {actual_scores}/{expected_scores} scores. Debate auto-completed due to judging timeout."
            self.db.add(AuditLog(
                debate_id=debate.id,
                event_type="judging_timeout_partial",
                event_data={
                    "reason": "partial_judge_scores",
                    "expected_scores": expected_scores,
                    "actual_scores": actual_scores,
                },
                actor_type="system",
            ))
            return {"debate_id": debate.id, "action": "completed", "reason": "judging_timeout_partial_scores"}
        return None
