"""Tournament advancement engine.

Handles:
- Winner advancement to next round
- Bye handling
- Tournament completion detection
- Match scheduling
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
from enum import Enum

from sqlalchemy.orm import Session

from src.database import get_db_session
from src.tournaments.bracket import (
    TournamentBracket, BracketType, SlotStatus,
    Match, BracketSlot, TournamentBracketGenerator
)


class AdvancementError(Exception):
    """Error during tournament advancement."""
    pass


class MatchNotReadyError(AdvancementError):
    """Match not ready to be decided."""
    pass


class AdvancementEngine:
    """Engine for managing tournament progression.
    
    Usage:
        engine = AdvancementEngine()
        
        # Record match result
        result = engine.record_match_result(
            match_id="m123",
            winner_id="agent_xyz",
            debate_id="debate_456",
        )
        
        # Check if tournament is complete
        if engine.is_tournament_complete("tournament_123"):
            winner = engine.get_tournament_winner("tournament_123")
    """
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or get_db_session()
        self.generator = TournamentBracketGenerator()
    
    def record_match_result(
        self,
        match_id: str,
        winner_id: str,
        loser_id: str,
        debate_id: Optional[str] = None,
        score: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Record the result of a match and advance winner.
        
        Args:
            match_id: The completed match ID
            winner_id: Winning participant ID
            loser_id: Losing participant ID
            debate_id: Optional debate ID for reference
            score: Optional score breakdown
        
        Returns:
            Advancement result with next match info
        """
        # Get match from database
        match = self._get_match(match_id)
        if not match:
            raise AdvancementError(f"Match not found: {match_id}")
        
        # Verify both participants were present
        if not match.slot_a_id or not match.slot_b_id:
            raise AdvancementError(f"Match {match_id} has empty slots")
        
        # Verify winner is one of the participants
        slot_a = self._get_slot(match.slot_a_id)
        slot_b = self._get_slot(match.slot_b_id)
        
        if winner_id not in [slot_a.participant_id, slot_b.participant_id]:
            raise AdvancementError(f"Winner {winner_id} not in match {match_id}")
        
        loser_slot = slot_a if slot_a.participant_id == winner_id else slot_b
        winner_slot = slot_b if slot_a.participant_id == winner_id else slot_a
        
        # Update match status
        self._complete_match(
            match_id=match_id,
            winner_slot_id=winner_slot.slot_id,
            loser_slot_id=loser_slot.slot_id,
            debate_id=debate_id,
        )
        
        # Find next match
        next_match = self._get_next_match(match_id)
        next_match_info = None
        
        if next_match:
            # Advance winner to next match
            self._advance_to_next_round(
                winner_id=winner_id,
                winner_name=winner_slot.participant_name,
                next_match=next_match,
            )
            
            next_match_info = {
                "match_id": next_match.match_id,
                "round": next_match.round_num,
                "position": next_match.bracket_position,
            }
        else:
            # Tournament complete - this was the final
            self._complete_tournament(match.tournament_id, winner_id)
        
        return {
            "match_id": match_id,
            "winner_id": winner_id,
            "loser_id": loser_id,
            "debate_id": debate_id,
            "next_match": next_match_info,
            "tournament_complete": next_match_info is None,
            "score": score,
        }
    
    def auto_advance_bye(
        self,
        match_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Handle bye - auto-advance single participant.
        
        When a match has only one participant (bye),
        auto-advance them to next round.
        """
        match = self._get_match(match_id)
        if not match:
            return None
        
        # Determine which slot has the participant
        slot_a = self._get_slot(match.slot_a_id) if match.slot_a_id else None
        slot_b = self._get_slot(match.slot_b_id) if match.slot_b_id else None
        
        # Find the filled slot
        filled_slot = None
        if slot_a and slot_a.is_filled():
            filled_slot = slot_a
        elif slot_b and slot_b.is_filled():
            filled_slot = slot_b
        
        if not filled_slot:
            return None
        
        # Update match
        self._complete_match(
            match_id=match_id,
            winner_slot_id=filled_slot.slot_id,
            loser_slot_id=None,  # No loser with bye
        )
        
        # Advance to next
        next_match = self._get_next_match(match_id)
        if next_match:
            self._advance_to_next_round(
                winner_id=filled_slot.participant_id,
                winner_name=filled_slot.participant_name,
                next_match=next_match,
            )
            
            return {
                "match_id": match_id,
                "winner_id": filled_slot.participant_id,
                "bye": True,
                "next_match": {
                    "match_id": next_match.match_id,
                    "round": next_match.round_num,
                },
            }
        
        return None
    
    def get_pending_matches(
        self,
        tournament_id: str,
    ) -> List[Dict[str, Any]]:
        """Get all matches that are ready to be played.
        
        A match is ready when both slots are filled.
        """
        # Query database for ready matches
        # Simplified for now
        return []
    
    def get_tournament_status(
        self,
        tournament_id: str,
    ) -> Dict[str, Any]:
        """Get current tournament status.
        
        Returns match counts, progress, etc.
        """
        # Query database
        # Simplified for now
        return {
            "tournament_id": tournament_id,
            "total_matches": 0,
            "completed_matches": 0,
            "pending_matches": 0,
            "next_match": None,
        }
    
    def is_tournament_complete(
        self,
        tournament_id: str,
    ) -> bool:
        """Check if tournament is complete (final match played)."""
        # Check if final match is completed
        # Simplified
        return False
    
    def get_tournament_winner(
        self,
        tournament_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get the tournament winner."""
        # Query final match winner
        # Simplified
        return None
    
    def get_match_history(
        self,
        tournament_id: str,
    ) -> List[Dict[str, Any]]:
        """Get all completed matches in order."""
        # Query completed matches
        return []
    
    # ============== Database Operations ==============
    # These would be implemented with actual database queries
    
    def _get_match(self, match_id: str) -> Optional[Match]:
        """Get match by ID."""
        # In production, query database
        return None
    
    def _get_slot(self, slot_id: str) -> Optional[BracketSlot]:
        """Get slot by ID."""
        # In production, query database
        return None
    
    def _get_next_match(self, current_match_id: str) -> Optional[Match]:
        """Get the next round match after this one."""
        # In production, query database
        # Match format: m0, m1, m2... in order
        # Next round matches follow current round
        return None
    
    def _complete_match(
        self,
        match_id: str,
        winner_slot_id: str,
        loser_slot_id: Optional[str],
        debate_id: Optional[str],
    ):
        """Mark match as completed in database."""
        # In production, update Match record
        pass
    
    def _advance_to_next_round(
        self,
        winner_id: str,
        winner_name: str,
        next_match: Match,
    ):
        """Advance winner to next round match."""
        # Find empty slot in next match
        # Update slot with winner info
        # In production, update database
        pass
    
    def _complete_tournament(
        self,
        tournament_id: str,
        winner_id: str,
    ):
        """Mark tournament as complete."""
        # In production, update Tournament status
        pass


class MatchScheduler:
    """Schedule matches within tournament.
    
    Handles timing and availability.
    """
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or get_db_session()
    
    def schedule_match(
        self,
        match_id: str,
        scheduled_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Schedule a match for a specific time.
        
        If no time provided, auto-schedule based on:
        - Round number (later rounds sooner)
        - Agent availability
        - Timezone handling
        """
        if scheduled_at is None:
            scheduled_at = self._calculate_next_slot(match_id)
        
        # Store schedule in database
        return {
            "match_id": match_id,
            "scheduled_at": scheduled_at.isoformat(),
        }
    
    def _calculate_next_slot(
        self,
        match_id: str,
    ) -> datetime:
        """Calculate next available slot for match.
        
        Default: 1 hour from now, minimum.
        """
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
    
    def reschedule_match(
        self,
        match_id: str,
        new_time: datetime,
    ) -> Dict[str, Any]:
        """Reschedule a match to a new time."""
        return {
            "match_id": match_id,
            "scheduled_at": new_time.isoformat(),
            "previous_time": None,  # Would query from DB
        }
    
    def get_upcoming_matches(
        self,
        tournament_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get upcoming scheduled matches."""
        return []


class BracketVisualizer:
    """Generate bracket visualizations.
    
    For web UI, exports, etc.
    """
    
    @staticmethod
    def to_json(bracket: TournamentBracket) -> Dict[str, Any]:
        """Export bracket as JSON for frontend."""
        return bracket.to_dict()
    
    @staticmethod
    def to_tree(bracket: TournamentBracket) -> Dict[str, Any]:
        """Export bracket as nested tree structure."""
        rounds = []
        
        for round_num in range(1, bracket.total_rounds + 1):
            matches = bracket.get_round_matches(round_num)
            
            round_data = {
                "round": round_num,
                "name": _get_round_display_name(round_num, bracket.total_rounds),
                "matches": [],
            }
            
            for match in matches:
                slot_a = bracket.get_slot(match.slot_a_id) if match.slot_a_id else None
                slot_b = bracket.get_slot(match.slot_b_id) if match.slot_b_id else None
                
                match_data = {
                    "id": match.match_id,
                    "status": match.status,
                    "slots": [
                        {
                            "id": slot_a.slot_id if slot_a else None,
                            "participant": slot_a.participant_name if slot_a else None,
                            "seed": slot_a.seed if slot_a else None,
                            "winner": match.winner_slot_id == slot_a.slot_id if match.winner_slot_id and slot_a else False,
                        } if slot_a else None,
                        {
                            "id": slot_b.slot_id if slot_b else None,
                            "participant": slot_b.participant_name if slot_b else None,
                            "seed": slot_b.seed if slot_b else None,
                            "winner": match.winner_slot_id == slot_b.slot_id if match.winner_slot_id and slot_b else False,
                        } if slot_b else None,
                    ],
                    "debate_id": match.debate_id,
                }
                
                round_data["matches"].append(match_data)
            
            rounds.append(round_data)
        
        return {
            "bracket_id": bracket.bracket_id,
            "tournament_id": bracket.tournament_id,
            "type": bracket.bracket_type.value,
            "rounds": rounds,
        }


def _get_round_display_name(round_num: int, total_rounds: int) -> str:
    """Get display name for a round."""
    remaining = total_rounds - round_num
    names = {
        0: "Finals",
        1: "Semi-Finals",
        2: "Quarter-Finals",
    }
    if remaining in names:
        return names[remaining]
    return f"Round {round_num}"
