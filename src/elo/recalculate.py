#!/usr/bin/env python3
"""Rating recalculation script.

Recalculates all ratings from historical debate data.
Useful for:
- Initial rating system population
- After bug fixes in rating calculation
- Rating system adjustments

Usage:
    python -m src.elo.recalculate --help
    python -m src.elo.recalculate --dry-run
    python -m src.elo.recalculate --recalc-all
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, ".")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Recalculate agent ratings from historical data"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making updates",
    )
    parser.add_argument(
        "--recalc-all",
        action="store_true",
        help="Recalculate all ratings from scratch",
    )
    parser.add_argument(
        "--agent",
        type=str,
        help="Recalculate only this agent",
    )
    parser.add_argument(
        "--debate",
        type=str,
        help="Recalculate ratings affected by this debate",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output results to JSON file",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output",
    )
    
    return parser.parse_args()


def get_debate_history(db) -> List[Dict[str, Any]]:
    """Get all completed debates for recalculation."""
    from src.models import Debate, Participant, Turn, Score, DebateStatus
    
    debates = db.query(Debate).filter(
        Debate.status == DebateStatus.COMPLETE
    ).all()
    
    results = []
    for debate in debates:
        # Get participants
        participants = {}
        for p in debate.participants:
            if p.agent_id:
                participants[p.id] = {
                    "participant_id": p.id,
                    "agent_id": p.agent_id,
                    "side": p.side.value if p.side else None,
                    "name": p.name,
                }
        
        # Get final scores
        scores = {}
        for score in debate.scores:
            pid = score.participant_id
            if pid not in scores:
                scores[pid] = []
            scores[pid].append(score.total_score)
        
        # Determine winner
        winner_side = debate.winner_side
        
        # Calculate team scores
        pro_score = 0
        con_score = 0
        pro_agents = []
        con_agents = []
        
        for p_id, p_info in participants.items():
            avg_score = sum(scores.get(p_id, [0])) / max(len(scores.get(p_id, [1])), 1)
            if p_info["side"] == "pro":
                pro_score += avg_score
                pro_agents.append(p_info["agent_id"])
            elif p_info["side"] == "con":
                con_score += avg_score
                con_agents.append(p_info["agent_id"])
        
        results.append({
            "debate_id": debate.id,
            "title": debate.title,
            "pro_agents": pro_agents,
            "con_agents": con_agents,
            "pro_score": pro_score,
            "con_score": con_score,
            "winner_side": winner_side.value if winner_side else None,
            "created_at": debate.created_at.isoformat() if debate.created_at else None,
        })
    
    return results


def recalculate_ratings_from_history(
    db,
    debates: List[Dict[str, Any]],
    initial_rating: int = 1500,
) -> Dict[str, int]:
    """Recalculate ratings based on debate history.
    
    Returns:
        Dict mapping agent_id to new rating
    """
    from src.elo.rating import EloRating
    from src.elo.storage import RatingStorage
    
    elo = EloRating()
    storage = RatingStorage(db)
    
    # Get current ratings (will be updated)
    current_ratings = {}
    for debate in debates:
        for agent_id in debate["pro_agents"] + debate["con_agents"]:
            if agent_id not in current_ratings:
                rating_info = storage.get_rating(agent_id)
                if rating_info:
                    current_ratings[agent_id] = rating_info.current_rating
                else:
                    current_ratings[agent_id] = initial_rating
    
    # Track rating changes
    changes = []
    
    # Process debates in order
    for debate in sorted(debates, key=lambda d: d["created_at"] or ""):
        pro_agents = debate["pro_agents"]
        con_agents = debate["con_agents"]
        
        if not pro_agents or not con_agents:
            continue
        
        # Calculate team ratings
        pro_avg = sum(current_ratings.get(a, initial_rating) for a in pro_agents) / len(pro_agents)
        con_avg = sum(current_ratings.get(a, initial_rating) for a in con_agents) / len(con_agents)
        
        # Determine outcome
        if debate["winner_side"] == "pro":
            pro_score = 1.0
            con_score = 0.0
        elif debate["winner_side"] == "con":
            pro_score = 0.0
            con_score = 1.0
        else:
            pro_score = 0.5
            con_score = 0.5
        
        # Calculate new team ratings
        pro_result, con_result = elo.calculate_ratings(
            pro_rating=int(pro_avg),
            con_rating=int(con_avg),
            pro_score=pro_score,
            con_score=con_score,
        )
        
        # Update individual ratings based on team change
        pro_change = pro_result.new_rating - pro_result.old_rating
        con_change = con_result.new_rating - con_result.old_rating
        
        for agent_id in pro_agents:
            current_ratings[agent_id] = max(100, current_ratings.get(agent_id, initial_rating) + pro_change)
            changes.append({
                "agent_id": agent_id,
                "debate_id": debate["debate_id"],
                "old_rating": current_ratings[agent_id] - pro_change,
                "new_rating": current_ratings[agent_id],
                "change": pro_change,
            })
        
        for agent_id in con_agents:
            current_ratings[agent_id] = max(100, current_ratings.get(agent_id, initial_rating) + con_change)
            changes.append({
                "agent_id": agent_id,
                "debate_id": debate["debate_id"],
                "old_rating": current_ratings[agent_id] - con_change,
                "new_rating": current_ratings[agent_id],
                "change": con_change,
            })
    
    return current_ratings, changes


def main():
    args = parse_args()
    
    from src.database import get_db_session
    
    db = get_db_session()
    
    try:
        # Get debate history
        print("Fetching debate history...")
        debates = get_debate_history(db)
        print(f"Found {len(debates)} completed debates")
        
        if not debates:
            print("No debates to recalculate")
            return 0
        
        if args.verbose:
            for d in debates:
                print(f"  {d['debate_id']}: {d['pro_agents']} vs {d['con_agents']} "
                      f"({d['pro_score']:.1f} - {d['con_score']:.1f})")
        
        # Recalculate
        print("\nRecalculating ratings...")
        new_ratings, changes = recalculate_ratings_from_history(db, debates)
        
        # Show results
        print(f"\nNew ratings ({len(new_ratings)} agents):")
        for agent_id, rating in sorted(new_ratings.items(), key=lambda x: -x[1]):
            print(f"  {agent_id}: {rating}")
        
        if args.output:
            with open(args.output, "w") as f:
                json.dump({
                    "recalculated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    "debates_processed": len(debates),
                    "new_ratings": new_ratings,
                    "changes": changes,
                }, f, indent=2)
            print(f"\nResults written to {args.output}")
        
        if args.dry_run:
            print("\n[Dry run - no changes made]")
            return 0
        
        # Apply changes
        print("\nApplying changes...")
        from src.elo.storage import RatingStorage
        storage = RatingStorage(db)
        
        for agent_id, new_rating in new_ratings.items():
            old_info = storage.get_rating(agent_id)
            old_rating = old_info.current_rating if old_info else 1500
            
            if old_rating != new_rating:
                print(f"  {agent_id}: {old_rating} -> {new_rating} ({new_rating - old_rating:+d})")
                
                # Find changes for this agent
                agent_changes = [c for c in changes if c["agent_id"] == agent_id]
                
                # Update storage
                for change in agent_changes:
                    storage.update_rating(
                        agent_id=agent_id,
                        new_rating=change["new_rating"],
                        old_rating=change["old_rating"],
                        debate_id=change["debate_id"],
                        outcome="win" if change["change"] > 0 else "loss",
                        reason="recalculation",
                    )
        
        print("\nRecalculation complete!")
        return 0
    
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
