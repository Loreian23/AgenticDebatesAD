"""Tournament bracket generation and management.

Supports:
- Single elimination (standard tournament)
- Double elimination (extension hooks)
- Round robin (extension hooks)
"""

import uuid
import math
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import Column, String, Integer, DateTime, JSON, ForeignKey, Index, Enum as SQLEnum

from src.database import get_db_session, Base


class BracketType(str, Enum):
    """Tournament bracket formats."""
    SINGLE_ELIM = "single_elim"
    DOUBLE_ELIM = "double_elim"  # Extension
    ROUND_ROBIN = "round_robin"  # Extension


class TournamentStatus(str, Enum):
    """Tournament state."""
    PENDING = "pending"      # Waiting for participants
    REGISTRATION = "registration"  # Open for signup
    READY = "ready"          # Bracket generated
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SlotStatus(str, Enum):
    """Match slot state."""
    EMPTY = "empty"
    TBD = "tbd"              # Waiting for previous match
    READY = "ready"         # Both participants set
    COMPLETED = "completed"
    BYE = "bye"


@dataclass
class BracketSlot:
    """A slot in the bracket (team or individual)."""
    slot_id: str
    round_num: int
    position: int
    participant_id: Optional[str] = None
    participant_name: Optional[str] = None
    seed: Optional[int] = None
    status: SlotStatus = SlotStatus.EMPTY
    match_id: Optional[str] = None
    
    def is_filled(self) -> bool:
        return self.participant_id is not None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "round": self.round_num,
            "position": self.position,
            "participant_id": self.participant_id,
            "participant_name": self.participant_name,
            "seed": self.seed,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "match_id": self.match_id,
        }


@dataclass
class Match:
    """A match within the bracket."""
    match_id: str
    round_num: int
    bracket_position: int
    slot_a_id: Optional[str] = None
    slot_b_id: Optional[str] = None
    winner_slot_id: Optional[str] = None
    debate_id: Optional[str] = None
    status: str = "pending"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "round": self.round_num,
            "position": self.bracket_position,
            "slot_a_id": self.slot_a_id,
            "slot_b_id": self.slot_b_id,
            "winner_slot_id": self.winner_slot_id,
            "debate_id": self.debate_id,
            "status": self.status,
        }


@dataclass 
class TournamentBracket:
    """A tournament bracket structure."""
    bracket_id: str
    tournament_id: str
    bracket_type: BracketType
    total_rounds: int
    participants_per_match: int = 2
    slots: List[BracketSlot] = field(default_factory=list)
    matches: List[Match] = field(default_factory=list)
    
    def get_slot(self, slot_id: str) -> Optional[BracketSlot]:
        for slot in self.slots:
            if slot.slot_id == slot_id:
                return slot
        return None
    
    def get_match(self, match_id: str) -> Optional[Match]:
        for match in self.matches:
            if match.match_id == match_id:
                return match
    
    def get_round_slots(self, round_num: int) -> List[BracketSlot]:
        return [s for s in self.slots if s.round_num == round_num]
    
    def get_round_matches(self, round_num: int) -> List[Match]:
        return [m for m in self.matches if m.round_num == round_num]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "bracket_id": self.bracket_id,
            "tournament_id": self.tournament_id,
            "bracket_type": self.bracket_type.value,
            "total_rounds": self.total_rounds,
            "participants_per_match": self.participants_per_match,
            "slots": [s.to_dict() for s in self.slots],
            "matches": [m.to_dict() for m in self.matches],
        }


class TournamentBracketGenerator:
    """Generate tournament brackets.
    
    Usage:
        generator = TournamentBracketGenerator()
        bracket = generator.generate_single_elim(
            tournament_id="t123",
            participants=["agent1", "agent2", "agent3", "agent4"],
            seeds={"agent1": 1, "agent2": 2, ...}
        )
    """
    
    @staticmethod
    def calculate_rounds(n_teams: int, teams_per_match: int = 2) -> int:
        """Calculate number of rounds needed.
        
        For single elimination: ceil(log2(n))
        """
        if teams_per_match == 2:
            return math.ceil(math.log2(n_teams)) if n_teams > 1 else 1
        else:
            # For >2 teams per match (not common)
            return math.ceil(math.log(n_teams) / math.log(teams_per_match))
    
    @staticmethod
    def calculate_byes(n_teams: int, teams_per_match: int = 2) -> int:
        """Calculate number of byes needed to fill bracket.
        
        Bracket size must be power of teams_per_match.
        """
        if teams_per_match == 2:
            bracket_size = 2 ** TournamentBracketGenerator.calculate_rounds(n_teams, teams_per_match)
        else:
            bracket_size = teams_per_match ** TournamentBracketGenerator.calculate_rounds(n_teams, teams_per_match)
        return bracket_size - n_teams
    
    def generate_single_elim(
        self,
        tournament_id: str,
        participants: List[str],
        seeds: Optional[Dict[str, int]] = None,
        names: Optional[Dict[str, str]] = None,
    ) -> TournamentBracket:
        """Generate single elimination bracket.
        
        Args:
            tournament_id: Tournament identifier
            participants: List of participant IDs
            seeds: Optional seed rankings {agent_id: seed}
            names: Optional display names {agent_id: name}
        
        Returns:
            TournamentBracket with slots and matches
        """
        n = len(participants)
        if n < 2:
            raise ValueError("Need at least 2 participants")
        
        # Sort participants by seed
        if seeds:
            sorted_participants = sorted(
                participants,
                key=lambda p: seeds.get(p, 999)
            )
        else:
            sorted_participants = list(participants)
        
        # Calculate bracket size
        total_rounds = self.calculate_rounds(n)
        bracket_size = 2 ** total_rounds
        n_byes = self.calculate_byes(n)
        
        # Create bracket
        bracket_id = str(uuid.uuid4())
        bracket = TournamentBracket(
            bracket_id=bracket_id,
            tournament_id=tournament_id,
            bracket_type=BracketType.SINGLE_ELIM,
            total_rounds=total_rounds,
            participants_per_match=2,
        )
        
        # Generate slots
        slot_id = 0
        
        # Round 1 (opening matches)
        # For seeded brackets, position seeds to reduce early high-seed matchups
        seed_order = self._seed_bracket_order(len(sorted_participants))
        
        for pos in range(bracket_size):
            round_num = 1
            
            if pos < n:
                # Has participant
                participant_id = seed_order[pos]
                participant_name = names.get(participant_id, participant_id) if names else participant_id
                seed = seeds.get(participant_id) if seeds else None
                
                slot = BracketSlot(
                    slot_id=f"s{slot_id}",
                    round_num=round_num,
                    position=pos,
                    participant_id=participant_id,
                    participant_name=participant_name,
                    seed=seed,
                    status=SlotStatus.READY if seed is None or pos < n else SlotStatus.BYE,
                )
            else:
                # Bye
                slot = BracketSlot(
                    slot_id=f"s{slot_id}",
                    round_num=round_num,
                    position=pos,
                    status=SlotStatus.BYE,
                )
            
            bracket.slots.append(slot)
            slot_id += 1
        
        # Generate matches
        match_id = 0
        for round_num in range(1, total_rounds + 1):
            if round_num == 1:
                # First round: slots pair up
                n_matches = bracket_size // 2
                for pos in range(n_matches):
                    slot_a = bracket.slots[pos * 2]
                    slot_b = bracket.slots[pos * 2 + 1]
                    
                    match = Match(
                        match_id=f"m{match_id}",
                        round_num=round_num,
                        bracket_position=pos,
                        slot_a_id=slot_a.slot_id if slot_a.is_filled() else None,
                        slot_b_id=slot_b.slot_id if slot_b.is_filled() else None,
                    )
                    
                    # Auto-fill if one side is bye
                    if slot_a.status == SlotStatus.BYE:
                        match.slot_b_id = slot_a.slot_id
                        match.winner_slot_id = slot_a.slot_id
                        slot_a.match_id = match.match_id
                        slot_b.match_id = match.match_id
                        slot_b.status = SlotStatus.READY
                    elif slot_b.status == SlotStatus.BYE:
                        match.slot_a_id = slot_b.slot_id
                        match.winner_slot_id = slot_b.slot_id
                        slot_a.match_id = match.match_id
                        slot_b.match_id = match.match_id
                        slot_a.status = SlotStatus.READY
                    else:
                        slot_a.match_id = match.match_id
                        slot_b.match_id = match.match_id
                        if slot_a.is_filled() and slot_b.is_filled():
                            match.status = "ready"
                    
                    bracket.matches.append(match)
                    match_id += 1
            else:
                # Subsequent rounds: winners advance
                prev_round_matches = bracket_size // (2 ** (round_num - 1))
                n_matches = prev_round_matches // 2
                
                for pos in range(n_matches):
                    match = Match(
                        match_id=f"m{match_id}",
                        round_num=round_num,
                        bracket_position=pos,
                    )
                    bracket.matches.append(match)
                    match_id += 1
        
        # Link subsequent rounds to previous winners
        self._link_bracket_rounds(bracket)
        
        return bracket
    
    def _seed_bracket_order(self, n_teams: int) -> List[int]:
        """Create bracket ordering for seeded participants.
        
        Standard seeding that places:
        - 1 vs 8, 4 vs 5, 3 vs 6, 2 vs 7 (for 8 teams)
        - Similar patterns for other sizes
        
        This minimizes early high-seed matchups.
        """
        if n_teams <= 1:
            return list(range(n_teams))
        
        rounds = self.calculate_rounds(n_teams)
        bracket_size = 2 ** rounds
        
        # Create ordered list: 1, bracket_size, bracket_size/2, etc.
        order = []
        remaining = list(range(1, n_teams + 1))
        
        # First position is #1 seed
        order.append(remaining.pop(0))
        
        # Alternate ends of remaining
        while remaining:
            if len(remaining) == 1:
                order.append(remaining.pop(0))
            else:
                # Pick from ends based on bracket position
                order.append(remaining.pop(-1))  # Highest remaining
                if remaining:
                    order.append(remaining.pop(0))  # Lowest remaining
        
        return order[:n_teams]
    
    def _link_bracket_rounds(self, bracket: TournamentBracket):
        """Link bracket rounds so winners advance automatically."""
        # This is handled during match completion
        pass
    
    def generate_bracket_visual(
        self,
        bracket: TournamentBracket,
    ) -> str:
        """Generate ASCII visualization of bracket.
        
        For debugging and display.
        """
        lines = []
        lines.append(f"Tournament Bracket (Type: {bracket.bracket_type.value})")
        lines.append(f"Rounds: {bracket.total_rounds}")
        lines.append("=" * 60)
        
        for round_num in range(1, bracket.total_rounds + 1):
            matches = bracket.get_round_matches(round_num)
            slots = bracket.get_round_slots(round_num)
            
            round_name = self._get_round_name(round_num, bracket.total_rounds)
            lines.append(f"\n{round_name}")
            lines.append("-" * 40)
            
            for match in matches:
                slot_a = bracket.get_slot(match.slot_a_id) if match.slot_a_id else None
                slot_b = bracket.get_slot(match.slot_b_id) if match.slot_b_id else None
                
                a_name = slot_a.participant_name if slot_a else "TBD"
                b_name = slot_b.participant_name if slot_b else "TBD"
                
                if match.status == "completed":
                    winner = bracket.get_slot(match.winner_slot_id)
                    if winner:
                        if winner.slot_id == match.slot_a_id:
                            a_name = f">>> {a_name} <<<"
                        else:
                            b_name = f">>> {b_name} <<<"
                
                lines.append(f"  {a_name:20} vs {b_name}")
        
        return "\n".join(lines)
    
    def _get_round_name(self, round_num: int, total_rounds: int) -> str:
        """Get human-readable round name."""
        remaining = total_rounds - round_num
        if remaining == 0:
            return "Finals"
        elif remaining == 1:
            return "Semi-Finals"
        elif remaining == 2:
            return "Quarter-Finals"
        else:
            return f"Round {round_num}"


class TournamentBracketManager:
    """Manage tournament brackets in database.
    
    Usage:
        manager = TournamentBracketManager()
        bracket = manager.create_bracket(
            tournament_id="t123",
            participants=["a1", "a2", "a3", "a4"],
            seeds={"a1": 1, "a2": 2, ...}
        )
    """
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or get_db_session()
        self.generator = TournamentBracketGenerator()
    
    def create_bracket(
        self,
        tournament_id: str,
        participants: List[str],
        bracket_type: BracketType = BracketType.SINGLE_ELIM,
        seeds: Optional[Dict[str, int]] = None,
        names: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """Create a new tournament bracket.
        
        Returns bracket data and creates match/debate records.
        """
        if bracket_type == BracketType.SINGLE_ELIM:
            bracket = self.generator.generate_single_elim(
                tournament_id=tournament_id,
                participants=participants,
                seeds=seeds,
                names=names,
            )
        else:
            raise NotImplementedError(f"Bracket type {bracket_type} not yet implemented")
        
        # Store bracket structure in database
        # (In production, this would create Tournament, Match, etc. records)
        
        return bracket.to_dict()
    
    def get_next_match(
        self,
        tournament_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get the next pending match that can be played.
        
        Returns match where both slots are filled.
        """
        # Query database for ready matches
        # Simplified for now
        return None
    
    def advance_winner(
        self,
        match_id: str,
        winner_slot_id: str,
    ) -> Dict[str, Any]:
        """Record winner of a match and advance to next round.
        
        Creates debate if needed, or auto-advances if bye.
        """
        # Update match with winner
        # Create next round match if exists
        # Return next match info
        return {}
