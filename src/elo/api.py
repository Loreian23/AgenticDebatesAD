"""Elo Rating API endpoints."""

from typing import Optional, Dict, Any, List
from datetime import datetime
import os
import hmac

from fastapi import APIRouter, HTTPException, Depends, Query, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.database import get_db
from src.elo.rating import EloRating, MatchOutcome
from src.elo.storage import RatingStorage, AgentRatingInfo


# ============== Request/Response Models ==============

class RatingResponse(BaseModel):
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


class RatingHistoryResponse(BaseModel):
    agent_id: str
    history: List[Dict[str, Any]]
    count: int


class LeaderboardResponse(BaseModel):
    leaderboard: List[Dict[str, Any]]
    count: int


class StatsResponse(BaseModel):
    total_agents: int
    active_agents: int
    total_games: int
    avg_rating: float
    min_rating: int
    max_rating: int
    default_rating: int


class UpdateRatingsRequest(BaseModel):
    debate_id: str
    pro_agents: List[str]
    con_agents: List[str]
    pro_score: float
    con_score: float
    winner_side: Optional[str] = None  # "pro", "con", or None for draw


class RecalculateRequest(BaseModel):
    agent_id: Optional[str] = None
    debate_id: Optional[str] = None
    dry_run: bool = False


async def require_admin_auth(
    authorization: Optional[str] = Header(None),
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Admin authorization required")

    expected = os.getenv("ADMIN_API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="ADMIN_API_KEY is not configured")

    admin_token = authorization[7:]
    if not hmac.compare_digest(admin_token, expected):
        raise HTTPException(status_code=403, detail="Invalid admin token")

    return {"admin": True}


# ============== Router ==============

router = APIRouter(prefix="/api", tags=["ratings"])


# ============== Rating Endpoints ==============

@router.get("/agents/{agent_id}/rating", response_model=RatingResponse)
def get_agent_rating(
    agent_id: str,
    db: Session = Depends(get_db),
):
    """Get current rating for an agent."""
    storage = RatingStorage(db)
    rating = storage.get_rating(agent_id)
    
    if not rating:
        raise HTTPException(status_code=404, detail="Agent rating not found")
    
    return RatingResponse(
        agent_id=rating.agent_id,
        current_rating=rating.current_rating,
        games_played=rating.games_played,
        wins=rating.wins,
        losses=rating.losses,
        draws=rating.draws,
        win_rate=rating.win_rate,
        avg_score=rating.avg_score,
        highest_rating=rating.highest_rating,
        lowest_rating=rating.lowest_rating,
        status=rating.status,
        last_game_at=rating.last_game_at,
    )


@router.get("/agents/{agent_id}/rating/history", response_model=RatingHistoryResponse)
def get_rating_history(
    agent_id: str,
    limit: int = Query(100, le=500),
    debate_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Get rating history for an agent."""
    storage = RatingStorage(db)
    history = storage.get_history(agent_id, limit=limit, debate_id=debate_id)
    
    return RatingHistoryResponse(
        agent_id=agent_id,
        history=history,
        count=len(history),
    )


@router.get("/ratings/leaderboard", response_model=LeaderboardResponse)
def get_leaderboard(
    limit: int = Query(50, le=100),
    min_games: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Get rating leaderboard."""
    storage = RatingStorage(db)
    leaderboard = storage.get_leaderboard(limit=limit, min_games=min_games)
    
    return LeaderboardResponse(
        leaderboard=leaderboard,
        count=len(leaderboard),
    )


@router.get("/ratings/stats", response_model=StatsResponse)
def get_rating_stats(
    db: Session = Depends(get_db),
):
    """Get rating system statistics."""
    storage = RatingStorage(db)
    stats = storage.get_statistics()
    
    return StatsResponse(**stats)


@router.post("/debates/{debate_id}/update-ratings")
def update_debate_ratings(
    debate_id: str,
    db: Session = Depends(get_db),
):
    """Update ratings for all participants in a completed debate.
    
    This is called after a debate is finalized to update ratings.
    """
    from src.models import Debate, Participant, Score, DebateStatus
    
    # Get debate
    debate = db.query(Debate).filter(Debate.id == debate_id).first()
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")
    
    if debate.status != DebateStatus.COMPLETE:
        raise HTTPException(status_code=400, detail="Debate not complete")
    
    # Get participants
    pro_agents = []
    con_agents = []
    pro_total_score = 0
    con_total_score = 0
    
    for p in debate.participants:
        if not p.agent_id:
            continue
        
        # Calculate average score for this participant
        scores = [s for s in debate.scores if s.participant_id == p.id]
        if scores:
            avg_score = sum(s.total_score for s in scores) / len(scores)
        else:
            avg_score = 0.5  # Default if no scores
        
        if p.side.value == "pro":
            pro_agents.append({"agent_id": p.agent_id, "score": avg_score})
            pro_total_score += avg_score
        elif p.side.value == "con":
            con_agents.append({"agent_id": p.agent_id, "score": avg_score})
            con_total_score += avg_score
    
    if not pro_agents or not con_agents:
        raise HTTPException(status_code=400, detail="No agent participants on both sides")
    
    # Determine outcome
    if debate.winner_side and debate.winner_side.value == "pro":
        pro_score = 1.0
        con_score = 0.0
    elif debate.winner_side and debate.winner_side.value == "con":
        pro_score = 0.0
        con_score = 1.0
    else:
        pro_score = 0.5
        con_score = 0.5
    
    # Calculate ratings
    elo = EloRating()
    storage = RatingStorage(db)
    
    results = []
    
    # Get average ratings
    pro_ratings = [storage.get_or_create_rating(a["agent_id"]).current_rating for a in pro_agents]
    con_ratings = [storage.get_or_create_rating(a["agent_id"]).current_rating for a in con_agents]
    
    pro_avg = sum(pro_ratings) / len(pro_ratings) if pro_ratings else 1500
    con_avg = sum(con_ratings) / len(con_ratings) if con_ratings else 1500
    
    pro_result, con_result = elo.calculate_ratings(
        pro_rating=int(pro_avg),
        con_rating=int(con_avg),
        pro_score=pro_score,
        con_score=con_score,
    )
    
    # Apply changes
    for i, agent_info in enumerate(pro_agents):
        old_rating = storage.get_or_create_rating(agent_info["agent_id"]).current_rating
        change = pro_result.new_rating - pro_result.old_rating
        new_rating = max(100, min(4000, old_rating + change))
        
        outcome = "win" if pro_score > con_score else ("loss" if pro_score < con_score else "draw")
        
        storage.update_rating(
            agent_id=agent_info["agent_id"],
            new_rating=new_rating,
            old_rating=old_rating,
            debate_id=debate_id,
            opponent_id=con_agents[0]["agent_id"] if con_agents else None,
            side="pro",
            outcome=outcome,
            expected_score=pro_result.expected_score,
            actual_score=pro_score,
            k_factor=pro_result.k_factor,
            reason="match",
        )
        
        results.append({
            "agent_id": agent_info["agent_id"],
            "old_rating": old_rating,
            "new_rating": new_rating,
            "change": new_rating - old_rating,
            "side": "pro",
            "outcome": outcome,
        })
    
    for i, agent_info in enumerate(con_agents):
        old_rating = storage.get_or_create_rating(agent_info["agent_id"]).current_rating
        change = con_result.new_rating - con_result.old_rating
        new_rating = max(100, min(4000, old_rating + change))
        
        outcome = "win" if con_score > pro_score else ("loss" if con_score < pro_score else "draw")
        
        storage.update_rating(
            agent_id=agent_info["agent_id"],
            new_rating=new_rating,
            old_rating=old_rating,
            debate_id=debate_id,
            opponent_id=pro_agents[0]["agent_id"] if pro_agents else None,
            side="con",
            outcome=outcome,
            expected_score=con_result.expected_score,
            actual_score=con_score,
            k_factor=con_result.k_factor,
            reason="match",
        )
        
        results.append({
            "agent_id": agent_info["agent_id"],
            "old_rating": old_rating,
            "new_rating": new_rating,
            "change": new_rating - old_rating,
            "side": "con",
            "outcome": outcome,
        })
    
    return {
        "debate_id": debate_id,
        "results": results,
        "match_outcome": {
            "pro_score": pro_score,
            "con_score": con_score,
            "winner": debate.winner_side.value if debate.winner_side else "draw",
        },
    }


@router.post("/ratings/recalculate")
def recalculate_ratings(
    req: RecalculateRequest,
    db: Session = Depends(get_db),
    _: Dict = Depends(require_admin_auth),
):
    """Recalculate ratings (admin only).
    
    If agent_id specified, recalculate only that agent.
    If debate_id specified, recalculate all agents in that debate.
    If neither, recalculate all ratings.
    """
    # TODO: Add admin auth
    storage = RatingStorage(db)
    
    if req.dry_run:
        return {"message": "Dry run - no changes made", "dry_run": True}
    
    if req.agent_id:
        # Recalculate single agent - fetch their history and replay
        # For simplicity, just reset and let them rebuild
        storage.reset_rating(req.agent_id)
        return {
            "message": f"Reset rating for {req.agent_id}",
            "agent_id": req.agent_id,
        }
    
    if req.debate_id:
        # Recalculate based on single debate
        return {
            "message": "Debate recalculation not yet implemented",
            "debate_id": req.debate_id,
        }
    
    # Full recalculation - this is expensive
    # In production, this should be done via CLI
    return {
        "message": "Full recalculation should be done via CLI",
        "use": "python -m src.elo.recalculate --recalc-all",
    }


@router.post("/ratings/reset/{agent_id}")
def reset_agent_rating(
    agent_id: str,
    db: Session = Depends(get_db),
    _: Dict = Depends(require_admin_auth),
):
    """Reset an agent's rating to default (admin only).
    
    TODO: Add admin auth
    """
    storage = RatingStorage(db)
    rating = storage.reset_rating(agent_id)
    
    return {
        "agent_id": agent_id,
        "new_rating": rating.current_rating,
        "message": "Rating reset to default (1500)",
    }
