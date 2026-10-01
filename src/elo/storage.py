"""Rating storage and history for Elo system.

Provides:
- Rating persistence to database
- Historical rating tracking
- Rating retrieval and queries
- Bulk operations for recalculation
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass

from sqlalchemy.orm import Session
from sqlalchemy import Column, String, Integer, DateTime, Float, JSON, Index, Enum as SQLEnum

from src.database import get_db_session, Base


class RatingStatus:
    """Rating status."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    PROVISIONAL = "provisional"


class AgentRating(Base):
    """Current rating for an agent."""
    __tablename__ = "agent_ratings"
    
    id = Column(String(36), primary_key=True)
    agent_id = Column(String(255), nullable=False, unique=True, index=True)
    
    # Current rating
    current_rating = Column(Integer, nullable=False, default=1500)
    rating_deviation = Column(Integer, default=350)  # RD (rating deviation)
    
    # Stats
    games_played = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    draws = Column(Integer, default=0)
    
    # Performance
    highest_rating = Column(Integer, default=1500)
    lowest_rating = Column(Integer, default=1500)
    avg_score = Column(Float, default=0.5)
    
    # Status
    status = Column(String(20), default=RatingStatus.ACTIVE)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_game_at = Column(DateTime, nullable=True)
    
    # Volatility (for Glicko-2, stored for future use)
    volatility = Column(Float, default=0.06)
    
    __table_args__ = (
        Index('idx_rating_agent', 'agent_id'),
    )


class RatingHistory(Base):
    """Historical rating changes."""
    __tablename__ = "rating_history"
    
    id = Column(String(36), primary_key=True)
    agent_id = Column(String(255), nullable=False, index=True)
    debate_id = Column(String(36), nullable=True, index=True)
    
    # Rating change
    old_rating = Column(Integer, nullable=False)
    new_rating = Column(Integer, nullable=False)
    change = Column(Integer, nullable=False)
    
    # Context
    opponent_id = Column(String(255), nullable=True)
    side = Column(String(20), nullable=True)  # pro, con, judge
    match_outcome = Column(String(20), nullable=True)  # win, loss, draw
    
    # Score info
    expected_score = Column(Float, nullable=True)
    actual_score = Column(Float, nullable=True)
    
    # K-factor used
    k_factor = Column(Integer, nullable=True)
    
    # Metadata
    timestamp = Column(DateTime, default=datetime.utcnow)
    reason = Column(String(50), nullable=True)  # 'match', 'adjustment', 'recalculation'
    
    __table_args__ = (
        Index('idx_history_agent', 'agent_id'),
        Index('idx_history_debate', 'debate_id'),
        Index('idx_history_time', 'timestamp'),
    )


@dataclass
class AgentRatingInfo:
    """Agent rating information."""
    agent_id: str
    current_rating: int
    games_played: int
    wins: int
    losses: int
    draws: int
    win_rate: float
    avg_score: float
    highest_rating: int
    lowest_rating: int
    status: str
    last_game_at: Optional[str]


class RatingStorage:
    """Store and retrieve agent ratings.
    
    Usage:
        storage = RatingStorage()
        
        # Get agent rating
        rating = storage.get_rating("agent_xyz")
        
        # Update after debate
        storage.update_rating(
            agent_id="agent_xyz",
            new_rating=1520,
            opponent_id="agent_abc",
            debate_id="debate_123",
            outcome="win",
        )
    """
    
    DEFAULT_RATING = 1500
    DEFAULT_RD = 350  # Rating Deviation
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or get_db_session()
    
    def get_rating(self, agent_id: str) -> Optional[AgentRatingInfo]:
        """Get current rating info for an agent."""
        rating = self.db.query(AgentRating).filter(
            AgentRating.agent_id == agent_id
        ).first()
        
        if not rating:
            return None
        
        return AgentRatingInfo(
            agent_id=rating.agent_id,
            current_rating=rating.current_rating,
            games_played=rating.games_played,
            wins=rating.wins,
            losses=rating.losses,
            draws=rating.draws,
            win_rate=rating.wins / rating.games_played if rating.games_played > 0 else 0.0,
            avg_score=rating.avg_score,
            highest_rating=rating.highest_rating,
            lowest_rating=rating.lowest_rating,
            status=rating.status,
            last_game_at=rating.last_game_at.isoformat() if rating.last_game_at else None,
        )
    
    def get_or_create_rating(
        self,
        agent_id: str,
        initial_rating: int = DEFAULT_RATING,
    ) -> AgentRating:
        """Get existing rating or create new one."""
        rating = self.db.query(AgentRating).filter(
            AgentRating.agent_id == agent_id
        ).first()
        
        if not rating:
            rating = AgentRating(
                id=str(uuid.uuid4()),
                agent_id=agent_id,
                current_rating=initial_rating,
                rating_deviation=self.DEFAULT_RD,
                highest_rating=initial_rating,
                lowest_rating=initial_rating,
            )
            self.db.add(rating)
            self.db.commit()
            self.db.refresh(rating)
        
        return rating
    
    def update_rating(
        self,
        agent_id: str,
        new_rating: int,
        old_rating: int,
        debate_id: Optional[str] = None,
        opponent_id: Optional[str] = None,
        side: Optional[str] = None,
        outcome: Optional[str] = None,
        expected_score: Optional[float] = None,
        actual_score: Optional[float] = None,
        k_factor: Optional[int] = None,
        reason: str = "match",
    ) -> AgentRating:
        """Update agent rating after a debate.
        
        Records the change in history and updates stats.
        """
        rating = self.get_or_create_rating(agent_id)
        
        # Update current rating
        rating.current_rating = new_rating
        rating.games_played += 1
        rating.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        rating.last_game_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Update stats based on outcome
        if outcome == "win":
            rating.wins += 1
        elif outcome == "loss":
            rating.losses += 1
        elif outcome == "draw":
            rating.draws += 1
        
        # Update performance
        total = rating.wins + rating.losses + rating.draws
        rating.avg_score = (rating.wins * 1.0 + rating.draws * 0.5) / total if total > 0 else 0.5
        
        # Update high/low
        if new_rating > rating.highest_rating:
            rating.highest_rating = new_rating
        if new_rating < rating.lowest_rating:
            rating.lowest_rating = new_rating
        
        # Update RD (decreases with games)
        rating.rating_deviation = max(50, rating.rating_deviation - 10)
        
        self.db.commit()
        self.db.refresh(rating)
        
        # Record history
        self._add_history(
            agent_id=agent_id,
            debate_id=debate_id,
            old_rating=old_rating,
            new_rating=new_rating,
            opponent_id=opponent_id,
            side=side,
            outcome=outcome,
            expected_score=expected_score,
            actual_score=actual_score,
            k_factor=k_factor,
            reason=reason,
        )
        
        return rating
    
    def _add_history(
        self,
        agent_id: str,
        old_rating: int,
        new_rating: int,
        debate_id: Optional[str],
        opponent_id: Optional[str],
        side: Optional[str],
        outcome: Optional[str],
        expected_score: Optional[float],
        actual_score: Optional[float],
        k_factor: Optional[int],
        reason: str,
    ):
        """Add entry to rating history."""
        history = RatingHistory(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            debate_id=debate_id,
            old_rating=old_rating,
            new_rating=new_rating,
            change=new_rating - old_rating,
            opponent_id=opponent_id,
            side=side,
            match_outcome=outcome,
            expected_score=expected_score,
            actual_score=actual_score,
            k_factor=k_factor,
            reason=reason,
            timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        self.db.add(history)
        self.db.commit()
    
    def get_history(
        self,
        agent_id: str,
        limit: int = 100,
        debate_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get rating history for an agent."""
        query = self.db.query(RatingHistory).filter(
            RatingHistory.agent_id == agent_id
        )
        
        if debate_id:
            query = query.filter(RatingHistory.debate_id == debate_id)
        
        history = query.order_by(RatingHistory.timestamp.desc()).limit(limit).all()
        
        return [
            {
                "id": h.id,
                "debate_id": h.debate_id,
                "old_rating": h.old_rating,
                "new_rating": h.new_rating,
                "change": h.change,
                "opponent_id": h.opponent_id,
                "side": h.side,
                "outcome": h.match_outcome,
                "expected_score": h.expected_score,
                "actual_score": h.actual_score,
                "k_factor": h.k_factor,
                "timestamp": h.timestamp.isoformat(),
                "reason": h.reason,
            }
            for h in history
        ]
    
    def get_leaderboard(
        self,
        limit: int = 50,
        min_games: int = 0,
    ) -> List[Dict[str, Any]]:
        """Get top agents by rating."""
        query = self.db.query(AgentRating).filter(
            AgentRating.games_played >= min_games,
            AgentRating.status == RatingStatus.ACTIVE,
        ).order_by(AgentRating.current_rating.desc()).limit(limit)
        
        return [
            {
                "rank": i + 1,
                "agent_id": r.agent_id,
                "rating": r.current_rating,
                "games_played": r.games_played,
                "wins": r.wins,
                "losses": r.losses,
                "draws": r.draws,
                "win_rate": r.wins / r.games_played if r.games_played > 0 else 0,
                "avg_score": r.avg_score,
                "highest_rating": r.highest_rating,
            }
            for i, r in enumerate(query.all())
        ]
    
    def bulk_update_ratings(
        self,
        updates: List[Dict[str, Any]],
        reason: str = "recalculation",
    ) -> int:
        """Bulk update ratings (for recalculations).
        
        Args:
            updates: List of dicts with agent_id, new_rating, old_rating, etc.
            reason: Reason for update
        
        Returns:
            Number of ratings updated
        """
        count = 0
        for update in updates:
            self.update_rating(
                agent_id=update["agent_id"],
                new_rating=update["new_rating"],
                old_rating=update["old_rating"],
                debate_id=update.get("debate_id"),
                opponent_id=update.get("opponent_id"),
                side=update.get("side"),
                outcome=update.get("outcome"),
                expected_score=update.get("expected_score"),
                actual_score=update.get("actual_score"),
                k_factor=update.get("k_factor"),
                reason=reason,
            )
            count += 1
        
        return count
    
    def reset_rating(self, agent_id: str) -> AgentRating:
        """Reset an agent's rating to default."""
        rating = self.get_or_create_rating(agent_id)
        
        rating.current_rating = self.DEFAULT_RATING
        rating.rating_deviation = self.DEFAULT_RD
        rating.games_played = 0
        rating.wins = 0
        rating.losses = 0
        rating.draws = 0
        rating.highest_rating = self.DEFAULT_RATING
        rating.lowest_rating = self.DEFAULT_RATING
        rating.avg_score = 0.5
        rating.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        self.db.commit()
        self.db.refresh(rating)
        
        return rating
    
    def set_provisional(self, agent_id: str) -> AgentRating:
        """Mark agent as provisional (new/uncertain rating)."""
        rating = self.get_or_create_rating(agent_id)
        rating.status = RatingStatus.PROVISIONAL
        rating.rating_deviation = 500  # High uncertainty
        self.db.commit()
        self.db.refresh(rating)
        return rating
    
    def activate_rating(self, agent_id: str) -> AgentRating:
        """Mark agent as active (established rating)."""
        rating = self.db.query(AgentRating).filter(
            AgentRating.agent_id == agent_id
        ).first()
        
        if not rating:
            raise ValueError(f"No rating found for agent: {agent_id}")
        
        rating.status = RatingStatus.ACTIVE
        rating.rating_deviation = self.DEFAULT_RD
        self.db.commit()
        self.db.refresh(rating)
        return rating
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get overall rating system statistics."""
        total_agents = self.db.query(AgentRating).count()
        active_agents = self.db.query(AgentRating).filter(
            AgentRating.status == RatingStatus.ACTIVE
        ).count()
        
        # Rating distribution
        ratings = self.db.query(AgentRating.current_rating).all()
        if ratings:
            rating_values = [r[0] for r in ratings]
            avg_rating = sum(rating_values) / len(rating_values)
            min_rating = min(rating_values)
            max_rating = max(rating_values)
        else:
            avg_rating = min_rating = max_rating = self.DEFAULT_RATING
        
        total_games = self.db.query(AgentRating).with_entities(
            self.db.query(AgentRating).func.sum(AgentRating.games_played)
        ).scalar() or 0
        
        return {
            "total_agents": total_agents,
            "active_agents": active_agents,
            "total_games": total_games,
            "avg_rating": round(avg_rating, 1),
            "min_rating": min_rating,
            "max_rating": max_rating,
            "default_rating": self.DEFAULT_RATING,
        }
