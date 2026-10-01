"""Tournament Bracket System for Agent Debate.

This module provides:
- Single-elimination bracket generation
- Double-elimination bracket support (extension hooks)
- Round-robin tournament support (extension hooks)
- Winner advancement and bracket updates
- Tournament scheduling and management
"""

from src.tournaments.bracket import TournamentBracket, BracketType, BracketSlot
from src.tournaments.advancement import AdvancementEngine

__all__ = [
    "TournamentBracket",
    "BracketType",
    "BracketSlot",
    "AdvancementEngine",
]
