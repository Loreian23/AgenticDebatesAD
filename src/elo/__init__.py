"""Elo Rating System for Agent Debate.

This module provides:
- Elo rating calculation with K-factor
- Expected score computation
- Rating updates after debates
- Historical rating tracking
- Recalculation tools for historical data

Standard Elo formula:
    E(A) = 1 / (1 + 10^((R(B) - R(A)) / 400))
    R'(A) = R(A) + K * (S(A) - E(A))

Where:
- R(A) = Current rating of agent A
- R(B) = Current rating of agent B  
- E(A) = Expected score for A
- S(A) = Actual score (1 = win, 0.5 = draw, 0 = loss)
- K = K-factor (development coefficient)
"""

from src.elo.rating import EloRating, RatingResult
from src.elo.storage import RatingStorage

__all__ = [
    "EloRating",
    "RatingResult",
    "RatingStorage",
]
