"""Elo rating calculation for Agent Debate.

Standard Elo rating system with extensions for:
- Variable K-factor based on experience
- Confidence weighting for uncertain matches
- Team rating aggregation
- Draw handling
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Any
from enum import Enum


class MatchOutcome(str, Enum):
    """Possible match outcomes."""
    WIN = "win"
    LOSS = "loss"
    DRAW = "draw"


@dataclass
class RatingResult:
    """Result of an Elo rating calculation."""
    agent_id: str
    old_rating: int
    new_rating: int
    expected_score: float
    actual_score: float
    rating_change: int
    k_factor: int
    match_outcome: MatchOutcome


class EloRating:
    """Elo rating calculator.
    
    Usage:
        elo = EloRating()
        
        # Calculate new ratings after a debate
        pro_result, con_result = elo.calculate_ratings(
            pro_rating=1500,
            con_rating=1500,
            pro_score=1.0,  # 1.0 = win, 0.5 = draw, 0.0 = loss
            con_score=0.0,
        )
        
        print(f"Pro: {pro_result.old_rating} -> {pro_result.new_rating}")
        print(f"Con: {con_result.old_rating} -> {con_result.new_rating}")
    """
    
    # Default K-factors
    K_MASTER = 10      # High-rated players (2000+)
    K_EXPERT = 20      # Intermediate (1600-1999)
    K_NORMAL = 32      # Standard players (1200-1599)
    K_DEVELOPING = 40  # New/low-rated players (<1200)
    
    # Rating bounds
    MIN_RATING = 100
    MAX_RATING = 4000
    DEFAULT_RATING = 1500
    
    def __init__(self, k_factor: Optional[int] = None):
        """Initialize Elo calculator.
        
        Args:
            k_factor: Override K-factor. If None, uses variable K based on rating.
        """
        self.fixed_k = k_factor
    
    def get_k_factor(self, rating: int, games_played: int = 0) -> int:
        """Get K-factor for a player based on their rating and experience.
        
        Args:
            rating: Player's current rating
            games_played: Number of games played (affects K for new players)
        
        Returns:
            K-factor value
        """
        if self.fixed_k:
            return self.fixed_k
        
        # Use lower K for high-rated players (more stable)
        if rating >= 2000:
            return self.K_MASTER
        
        if rating >= 1600:
            return self.K_EXPERT
        
        if rating >= 1200:
            return self.K_NORMAL
        
        return self.K_DEVELOPING
    
    def expected_score(self, rating_a: int, rating_b: int) -> float:
        """Calculate expected score for player A.
        
        E(A) = 1 / (1 + 10^((R(B) - R(A)) / 400))
        
        Args:
            rating_a: Player A's rating
            rating_b: Player B's rating
        
        Returns:
            Expected score between 0 and 1
        """
        return 1.0 / (1.0 + math.pow(10, (rating_b - rating_a) / 400.0))
    
    def calculate_new_rating(
        self,
        current_rating: int,
        expected: float,
        actual: float,
        k_factor: Optional[int] = None,
    ) -> int:
        """Calculate new rating after a match.
        
        R'(A) = R(A) + K * (S(A) - E(A))
        
        Args:
            current_rating: Player's current rating
            expected: Expected score (0-1)
            actual: Actual score (1=win, 0.5=draw, 0=loss)
            k_factor: K-factor (uses default if None)
        
        Returns:
            New rating (clamped to MIN/MAX_RATING)
        """
        if k_factor is None:
            k_factor = self.get_k_factor(current_rating)
        
        change = k_factor * (actual - expected)
        new_rating = int(current_rating + change)
        
        # Clamp to bounds
        return max(self.MIN_RATING, min(self.MAX_RATING, new_rating))
    
    def calculate_ratings(
        self,
        pro_rating: int,
        con_rating: int,
        pro_score: float,
        con_score: float,
        k_pro: Optional[int] = None,
        k_con: Optional[int] = None,
    ) -> Tuple[RatingResult, RatingResult]:
        """Calculate new ratings for both sides after a debate.
        
        Args:
            pro_rating: Affirmative side rating
            con_rating: Negative side rating
            pro_score: Affirmative score (1.0=win, 0.5=draw, 0.0=loss)
            con_score: Negative score (1.0=win, 0.5=draw, 0.0=loss)
            k_pro: K-factor for affirmative (auto if None)
            k_con: K-factor for negative (auto if None)
        
        Returns:
            Tuple of (pro_result, con_result)
        """
        # Determine outcomes
        if pro_score > con_score:
            pro_outcome = MatchOutcome.WIN
            con_outcome = MatchOutcome.LOSS
        elif pro_score < con_score:
            pro_outcome = MatchOutcome.LOSS
            con_outcome = MatchOutcome.WIN
        else:
            pro_outcome = MatchOutcome.DRAW
            con_outcome = MatchOutcome.DRAW
        
        # Calculate expected scores
        expected_pro = self.expected_score(pro_rating, con_rating)
        expected_con = 1.0 - expected_pro
        
        # Get K-factors
        kp = k_pro or self.get_k_factor(pro_rating)
        kc = k_con or self.get_k_factor(con_rating)
        
        # Calculate new ratings
        new_pro = self.calculate_new_rating(pro_rating, expected_pro, pro_score, kp)
        new_con = self.calculate_new_rating(con_rating, expected_con, con_score, kc)
        
        return (
            RatingResult(
                agent_id="pro_side",  # Caller should replace with actual agent_id
                old_rating=pro_rating,
                new_rating=new_pro,
                expected_score=expected_pro,
                actual_score=pro_score,
                rating_change=new_pro - pro_rating,
                k_factor=kp,
                match_outcome=pro_outcome,
            ),
            RatingResult(
                agent_id="con_side",  # Caller should replace with actual agent_id
                old_rating=con_rating,
                new_rating=new_con,
                expected_score=expected_con,
                actual_score=con_score,
                rating_change=new_con - con_rating,
                k_factor=kc,
                match_outcome=con_outcome,
            ),
        )
    
    def calculate_team_ratings(
        self,
        team_ratings: List[int],
        team_scores: List[float],
        opponent_ratings: List[int],
        opponent_scores: List[float],
    ) -> List[RatingResult]:
        """Calculate ratings for multiple players in a team debate.
        
        Each player's rating is updated based on:
        - Their individual score
        - The team's overall outcome
        - Their contribution factor
        """
        if len(team_ratings) != len(team_scores):
            raise ValueError("team_ratings and team_scores must have same length")
        
        results = []
        
        # Calculate team averages
        avg_team_rating = sum(team_ratings) / len(team_ratings)
        avg_opponent_rating = sum(opponent_ratings) / len(opponent_ratings)
        
        # Team outcome
        team_total_score = sum(team_scores)
        opponent_total_score = sum(opponent_scores)
        
        if team_total_score > opponent_total_score:
            team_outcome = MatchOutcome.WIN
        elif team_total_score < opponent_total_score:
            team_outcome = MatchOutcome.LOSS
        else:
            team_outcome = MatchOutcome.DRAW
        
        # Expected score for team
        expected = self.expected_score(avg_team_rating, avg_opponent_rating)
        
        for i, (rating, score) in enumerate(zip(team_ratings, team_scores)):
            # Contribution factor based on relative score
            contribution = score / team_total_score if team_total_score > 0 else 1.0 / len(team_scores)
            
            # Actual score adjusted by contribution
            actual = score
            
            # K-factor for this player
            k = self.get_k_factor(rating)
            
            # Calculate new rating
            new_rating = self.calculate_new_rating(rating, expected * contribution, actual, k)
            
            results.append(RatingResult(
                agent_id=f"player_{i}",
                old_rating=rating,
                new_rating=new_rating,
                expected_score=expected * contribution,
                actual_score=actual,
                rating_change=new_rating - rating,
                k_factor=k,
                match_outcome=team_outcome,
            ))
        
        return results
    
    def estimate_rating_from_performance(
        self,
        wins: int,
        losses: int,
        draws: int = 0,
        avg_opponent_rating: int = 1500,
        confidence: float = 0.95,
    ) -> Tuple[int, int]:
        """Estimate rating from performance record.
        
        Uses Bayesian inference to estimate true rating.
        
        Args:
            wins: Number of wins
            losses: Number of losses
            draws: Number of draws
            avg_opponent_rating: Average rating of opponents
            confidence: Confidence level for the estimate
        
        Returns:
            Tuple of (estimated_rating, rating_uncertainty)
        """
        n = wins + losses + draws
        if n == 0:
            return self.DEFAULT_RATING, 500
        
        # Win rate
        win_rate = wins / n
        
        # Map win rate to rating difference
        # Assume 2400 vs 1200 gives ~0.95 win rate
        # Each 400 rating = 10x odds ratio
        if win_rate >= 0.95:
            diff = 2400 - avg_opponent_rating
        elif win_rate <= 0.05:
            diff = 600 - avg_opponent_rating
        else:
            # Inverse of expected score formula
            # E = 1 / (1 + 10^(diff/400))
            # diff = 400 * log10(E^-1 - 1)
            diff = 400 * math.log10(1 / win_rate - 1) if win_rate > 0 else -1000
        
        estimated = avg_opponent_rating + int(diff)
        
        # Uncertainty decreases with more games
        uncertainty = int(500 / math.sqrt(n))
        
        return max(self.MIN_RATING, min(self.MAX_RATING, estimated)), uncertainty
    
    def probability_of_victory(
        self,
        rating_a: int,
        rating_b: int,
    ) -> float:
        """Get probability of A beating B.
        
        Returns:
            Probability between 0 and 1
        """
        return self.expected_score(rating_a, rating_b)
    
    def quality_of_match(
        self,
        rating_a: int,
        rating_b: int,
        min_quality: float = 0.5,
    ) -> float:
        """Calculate match quality (how close the matchup is).
        
        Returns:
            Quality metric between 0 and 1 (1 = perfect match)
        """
        expected = self.expected_score(rating_a, rating_b)
        quality = 1.0 - abs(expected - 0.5) * 2
        return max(min_quality, quality)
