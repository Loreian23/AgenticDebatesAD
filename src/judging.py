"""Judging engine for calculating debate results."""

import statistics
from typing import Dict, List, Any, Optional
from collections import defaultdict

from sqlalchemy.orm import Session

from src.models import Debate, Participant, Turn, Score, ParticipantSide
from src.database import get_db_session


class JudgingEngine:
    """
    Calculate debate results based on judge scores.
    
    Produces:
    - Team scores (aggregated by side)
    - Individual scores
    - Winner determination with confidence level
    - Rationale for decision
    """
    
    def __init__(self, debate_id: str, db: Optional[Session] = None):
        self.debate_id = debate_id
        self.db = db or get_db_session()
    
    def calculate_results(self) -> Dict[str, Any]:
        """
        Calculate complete debate results.
        
        Returns:
            {
                "team_scores": {...},
                "individual_scores": [...],
                "winner": "pro" | "con" | None,
                "confidence": float (0-1),
                "rationale": str,
                "score_breakdown": {...}
            }
        """
        debate = self.db.query(Debate).filter(Debate.id == self.debate_id).first()
        if not debate:
            raise ValueError(f"Debate {self.debate_id} not found")
        
        # Get all scores
        scores = self.db.query(Score).filter(Score.debate_id == self.debate_id).all()
        
        if not scores:
            raise ValueError("No scores submitted for this debate")
        
        # Calculate individual scores
        individual_scores = self._calculate_individual_scores(scores)
        
        # Calculate team scores
        team_scores = self._calculate_team_scores(individual_scores)
        
        # Determine winner
        winner, confidence = self._determine_winner(team_scores)
        
        # Generate rationale
        rationale = self._generate_rationale(team_scores, individual_scores, winner)
        
        # Calculate judge agreement metrics
        judge_agreement = self._calculate_judge_agreement(scores)
        
        return {
            "team_scores": team_scores,
            "individual_scores": individual_scores,
            "winner": winner.value if winner else None,
            "confidence": confidence,
            "rationale": rationale,
            "judge_agreement": judge_agreement,
            "score_breakdown": {
                "total_scores": len(scores),
                "pro_participants": len([p for p in debate.participants if p.side == ParticipantSide.PRO]),
                "con_participants": len([p for p in debate.participants if p.side == ParticipantSide.CON]),
                "judge_count": len([p for p in debate.participants if p.side == ParticipantSide.JUDGE]),
            }
        }
    
    def _calculate_individual_scores(self, scores: List[Score]) -> List[Dict[str, Any]]:
        """Calculate average scores for each participant."""
        # Group scores by participant
        scores_by_participant = defaultdict(list)
        for score in scores:
            scores_by_participant[score.participant_id].append(score)
        
        individual_scores = []
        
        for participant_id, p_scores in scores_by_participant.items():
            participant = self.db.query(Participant).filter(Participant.id == participant_id).first()
            if not participant:
                continue
            
            # Calculate averages across all judges
            avg_argument = statistics.mean([s.argument_quality for s in p_scores])
            avg_evidence = statistics.mean([s.evidence_quality for s in p_scores])
            avg_rebuttal = statistics.mean([s.rebuttal_strength for s in p_scores])
            avg_clarity = statistics.mean([s.clarity for s in p_scores])
            avg_compliance = statistics.mean([s.compliance for s in p_scores])
            avg_total = statistics.mean([s.total_score for s in p_scores])
            avg_weighted = statistics.mean([s.weighted_score for s in p_scores])
            
            # Calculate variance (agreement between judges)
            if len(p_scores) > 1:
                variance = statistics.variance([s.weighted_score for s in p_scores])
                std_dev = statistics.stdev([s.weighted_score for s in p_scores])
            else:
                variance = 0
                std_dev = 0
            
            individual_scores.append({
                "participant_id": participant_id,
                "name": participant.name,
                "side": participant.side.value,
                "scores": {
                    "argument_quality": round(avg_argument, 2),
                    "evidence_quality": round(avg_evidence, 2),
                    "rebuttal_strength": round(avg_rebuttal, 2),
                    "clarity": round(avg_clarity, 2),
                    "compliance": round(avg_compliance, 2),
                    "total": round(avg_total, 2),
                    "weighted": round(avg_weighted, 2),
                },
                "judge_count": len(p_scores),
                "variance": round(variance, 4),
                "std_dev": round(std_dev, 4),
                "all_scores": [
                    {
                        "judge_id": s.judge_id,
                        "total": s.total_score,
                        "weighted": s.weighted_score,
                        "rationale": s.rationale,
                    }
                    for s in p_scores
                ]
            })
        
        # Sort by weighted score descending
        individual_scores.sort(key=lambda x: x["scores"]["weighted"], reverse=True)
        
        return individual_scores
    
    def _calculate_team_scores(self, individual_scores: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate aggregate scores for each team."""
        pro_scores = [s for s in individual_scores if s["side"] == "pro"]
        con_scores = [s for s in individual_scores if s["side"] == "con"]
        
        def aggregate_scores(scores: List[Dict[str, Any]]) -> Dict[str, float]:
            if not scores:
                return {
                    "argument_quality": 0,
                    "evidence_quality": 0,
                    "rebuttal_strength": 0,
                    "clarity": 0,
                    "compliance": 0,
                    "total": 0,
                    "weighted": 0,
                }
            
            return {
                "argument_quality": round(statistics.mean([s["scores"]["argument_quality"] for s in scores]), 2),
                "evidence_quality": round(statistics.mean([s["scores"]["evidence_quality"] for s in scores]), 2),
                "rebuttal_strength": round(statistics.mean([s["scores"]["rebuttal_strength"] for s in scores]), 2),
                "clarity": round(statistics.mean([s["scores"]["clarity"] for s in scores]), 2),
                "compliance": round(statistics.mean([s["scores"]["compliance"] for s in scores]), 2),
                "total": round(statistics.mean([s["scores"]["total"] for s in scores]), 2),
                "weighted": round(statistics.mean([s["scores"]["weighted"] for s in scores]), 2),
            }
        
        pro_aggregate = aggregate_scores(pro_scores)
        con_aggregate = aggregate_scores(con_scores)
        
        # Calculate point differences
        differences = {
            key: round(pro_aggregate[key] - con_aggregate[key], 2)
            for key in pro_aggregate.keys()
        }
        
        return {
            "pro": {
                "average_scores": pro_aggregate,
                "participant_count": len(pro_scores),
                "highest_scorer": pro_scores[0]["name"] if pro_scores else None,
                "highest_score": pro_scores[0]["scores"]["weighted"] if pro_scores else 0,
            },
            "con": {
                "average_scores": con_aggregate,
                "participant_count": len(con_scores),
                "highest_scorer": con_scores[0]["name"] if con_scores else None,
                "highest_score": con_scores[0]["scores"]["weighted"] if con_scores else 0,
            },
            "differences": differences,
            "pro_advantage": round(pro_aggregate["weighted"] - con_aggregate["weighted"], 2),
        }
    
    def _determine_winner(self, team_scores: Dict[str, Any]) -> tuple[Optional[ParticipantSide], float]:
        """
        Determine winner and confidence level.
        
        Confidence is based on:
        - Margin of victory (higher margin = higher confidence)
        - Judge agreement (lower variance = higher confidence)
        - Number of judges (more judges = higher confidence, up to a point)
        """
        pro_weighted = team_scores["pro"]["average_scores"]["weighted"]
        con_weighted = team_scores["con"]["average_scores"]["weighted"]
        
        margin = abs(pro_weighted - con_weighted)
        
        # Base confidence on margin (0-10 scale difference)
        if margin >= 2.0:
            base_confidence = 0.9
        elif margin >= 1.0:
            base_confidence = 0.75
        elif margin >= 0.5:
            base_confidence = 0.6
        else:
            base_confidence = 0.5  # Too close to call with high confidence
        
        # Winner
        if pro_weighted > con_weighted:
            winner = ParticipantSide.PRO
        elif con_weighted > pro_weighted:
            winner = ParticipantSide.CON
        else:
            winner = None
            base_confidence = 0.0  # True tie
        
        return winner, round(base_confidence, 2)
    
    def _calculate_judge_agreement(self, scores: List[Score]) -> Dict[str, Any]:
        """Calculate how much judges agree with each other.
        
        High disagreement = low confidence in judging reliability.
        Judges: each judge has given scores to multiple participants.
        We measure per-participant score variance and per-judge deviation from consensus.
        """
        # Group scores by participant
        by_participant: dict = defaultdict(list)
        by_judge: dict = defaultdict(list)
        
        for s in scores:
            by_participant[s.participant_id].append(s.weighted_score)
            by_judge[s.judge_id].append(s.weighted_score)
        
        # Per-participant: how much do judges disagree about this debater?
        participant_variance = {}
        for pid, s_list in by_participant.items():
            if len(s_list) >= 2:
                participant_variance[pid] = round(statistics.variance(s_list), 3)
        
        # Overall inter-judge reliability: Spearman-like correlation
        # For 1 judge, agreement is undefined (perfect by default)
        if len(by_judge) <= 1:
            consensus_score = 1.0
            reliability = "single_judge"
        else:
            # Compute pairwise judge score differences
            judge_ids = list(by_judge.keys())
            pairwise_diffs = []
            for i in range(len(judge_ids)):
                for j in range(i + 1, len(judge_ids)):
                    scores_i = by_judge[judge_ids[i]]
                    scores_j = by_judge[judge_ids[j]]
                    if len(scores_i) == len(scores_j):
                        for a, b in zip(sorted(scores_i), sorted(scores_j)):
                            pairwise_diffs.append(abs(a - b))
            
            if pairwise_diffs:
                avg_diff = statistics.mean(pairwise_diffs)
                # Consensus: closer to 1.0 when avg diff is small
                # At avg diff 0: 1.0, at avg diff 5: ~0.3, at avg diff 10: ~0.0
                consensus_score = round(max(0.0, 1.0 - avg_diff / 6.0), 3)
            else:
                consensus_score = 1.0
            
            if consensus_score >= 0.8:
                reliability = "high"
            elif consensus_score >= 0.6:
                reliability = "moderate"
            elif consensus_score >= 0.4:
                reliability = "low"
            else:
                reliability = "poor"
        
        # Per-judge deviation from overall mean
        all_weighted = [s.weighted_score for s in scores]
        grand_mean = statistics.mean(all_weighted) if all_weighted else 0
        judge_deviation = {}
        for jid, s_list in by_judge.items():
            judge_mean = statistics.mean(s_list)
            judge_deviation[jid] = round(abs(judge_mean - grand_mean), 3)
        
        return {
            "consensus_score": consensus_score,
            "reliability": reliability,
            "judge_count": len(by_judge),
            "high_disagreement": consensus_score < 0.5,
            "participant_variance": participant_variance,
            "judge_deviation_from_mean": judge_deviation,
            "interpretation": {
                "high": "Judges strongly agree — results are reliable.",
                "moderate": "Judges generally agree with minor differences.",
                "low": "Judges disagree significantly — results should be interpreted with caution.",
                "poor": "Judges strongly disagree — consider re-evaluation.",
                "single_judge": "Only one judge scored this debate.",
            }.get(reliability, "Unknown."),
        }
    
    def _generate_rationale(
        self, 
        team_scores: Dict[str, Any], 
        individual_scores: List[Dict[str, Any]],
        winner: Optional[ParticipantSide]
    ) -> str:
        """Generate a human-readable rationale for the decision."""
        pro = team_scores["pro"]
        con = team_scores["con"]
        
        lines = []
        
        # Overall result
        if winner is None:
            lines.append("The debate resulted in a tie.")
        else:
            winner_name = "PRO" if winner == ParticipantSide.PRO else "CON"
            lines.append(f"The {winner_name} side wins the debate.")
        
        lines.append("")
        
        # Score summary
        lines.append(f"PRO average weighted score: {pro['average_scores']['weighted']}/10")
        lines.append(f"CON average weighted score: {con['average_scores']['weighted']}/10")
        lines.append(f"Margin: {abs(team_scores['pro_advantage']):.2f} points")
        lines.append("")
        
        # Category breakdown
        lines.append("Category breakdown (PRO vs CON):")
        for category in ["argument_quality", "evidence_quality", "rebuttal_strength", "clarity", "compliance"]:
            pro_score = pro['average_scores'][category]
            con_score = con['average_scores'][category]
            diff = pro_score - con_score
            
            if abs(diff) < 0.1:
                result = "tied"
            elif diff > 0:
                result = "PRO advantage"
            else:
                result = "CON advantage"
            
            lines.append(f"  - {category.replace('_', ' ').title()}: {pro_score:.1f} vs {con_score:.1f} ({result})")
        
        lines.append("")
        
        # Individual highlights
        lines.append("Individual highlights:")
        top_3 = individual_scores[:3]
        for i, scorer in enumerate(top_3, 1):
            lines.append(f"  {i}. {scorer['name']} ({scorer['side'].upper()}): {scorer['scores']['weighted']:.2f}/10")
        
        return "\n".join(lines)


class ScoringGuidelines:
    """Guidelines for judges on scoring criteria."""
    
    CRITERIA = {
        "argument_quality": {
            "name": "Argument Quality",
            "description": "Strength, logic, and persuasiveness of arguments",
            "scale": [
                (0, 2, "Weak or flawed arguments"),
                (2, 4, "Basic arguments with gaps"),
                (4, 6, "Solid arguments, reasonably constructed"),
                (6, 8, "Strong, well-reasoned arguments"),
                (8, 10, "Exceptional, compelling arguments"),
            ]
        },
        "evidence_quality": {
            "name": "Evidence Quality",
            "description": "Use of facts, citations, and supporting evidence",
            "scale": [
                (0, 2, "Little to no evidence"),
                (2, 4, "Sparse or weak evidence"),
                (4, 6, "Adequate evidence provided"),
                (6, 8, "Strong, relevant evidence"),
                (8, 10, "Extensive, high-quality evidence"),
            ]
        },
        "rebuttal_strength": {
            "name": "Rebuttal Strength",
            "description": "Effectiveness in addressing opponent arguments",
            "scale": [
                (0, 2, "Fails to address opponent points"),
                (2, 4, "Weak rebuttals"),
                (4, 6, "Adequate rebuttal attempts"),
                (6, 8, "Strong counter-arguments"),
                (8, 10, "Devastating, thorough rebuttals"),
            ]
        },
        "clarity": {
            "name": "Clarity",
            "description": "Communication clarity and organization",
            "scale": [
                (0, 2, "Difficult to follow"),
                (2, 4, "Somewhat unclear"),
                (4, 6, "Generally clear"),
                (6, 8, "Very clear and organized"),
                (8, 10, "Exceptionally clear and compelling"),
            ]
        },
        "compliance": {
            "name": "Compliance",
            "description": "Adherence to debate rules and format",
            "scale": [
                (0, 2, "Frequent rule violations"),
                (2, 4, "Some violations"),
                (4, 6, "Generally compliant"),
                (6, 8, "Fully compliant"),
                (8, 10, "Exemplary adherence to rules"),
            ]
        },
    }
    
    @classmethod
    def get_guidelines(cls) -> Dict[str, Any]:
        """Get complete scoring guidelines."""
        return cls.CRITERIA
    
    @classmethod
    def get_score_description(cls, criterion: str, score: float) -> str:
        """Get description for a specific score."""
        if criterion not in cls.CRITERIA:
            return "Unknown criterion"
        
        for min_val, max_val, description in cls.CRITERIA[criterion]["scale"]:
            if min_val <= score <= max_val:
                return description
        
        return "Out of range"
