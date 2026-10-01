#!/usr/bin/env python3
"""Seed demo data: completed debates + ELO ratings for the Arena UI.

Creates realistic completed debates (with turns + judge scores + winners) so the
Arena home and leaderboard are not empty on first load. Run against an empty DB:

    python scripts/seed_demo.py

Idempotent: skips if completed debates already exist.
"""

import sys, os, uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import get_db_session, init_db
from src.models import (
    Debate, Participant, Turn, Score, DebateStatus, ParticipantSide, ParticipantType,
)
from src.elo.storage import RatingStorage


def _now():  # timezone-naive UTC (matches model server_default=func.now())
    return datetime.now(timezone.utc).replace(tzinfo=None)


ROSTER = [
    {"name": "Nova",     "provider": "DeepSeek V4 Pro", "elo": 1842},
    {"name": "Scarlett", "provider": "DeepSeek V4 Pro", "elo": 1761},
    {"name": "TEC",      "provider": "DeepSeek V4 Pro", "elo": 1708},
    {"name": "Veronica", "provider": "DeepSeek V4 Pro", "elo": 1655},
    {"name": "YuanYuan", "provider": "DeepSeek V4 Pro", "elo": 1612},
    {"name": "Jarvis",   "provider": "DeepSeek V4 Pro", "elo": 1590},
]

DEBATES = [
    {
        "title": "Ban autonomous weapons",
        "proposition": "Autonomous weapons systems should be banned under international law due to the inability to assign moral accountability for lethal decisions made by machines.",
        "pro": "TEC", "con": "YuanYuan", "judge": "Scarlett", "winner": "pro",
    },
    {
        "title": "CBDCs threaten financial privacy",
        "proposition": "Central Bank Digital Currencies pose an unacceptable threat to financial privacy and civil liberties, and should not be adopted.",
        "pro": "Scarlett", "con": "Nova", "judge": "TEC", "winner": "con",
    },
    {
        "title": "UBI should replace welfare",
        "proposition": "A universal basic income should replace existing means-tested welfare programs, simplifying the safety net and removing poverty traps.",
        "pro": "Veronica", "con": "Jarvis", "judge": "Nova", "winner": "pro",
    },
    {
        "title": "Open-source AI is safer",
        "proposition": "Open-source AI models are safer for society than closed-source models, because public scrutiny and auditability outweigh the risks of misuse.",
        "pro": "Nova", "con": "TEC", "judge": "Veronica", "winner": "con",
    },
    {
        "title": "Remote work is a net positive",
        "proposition": "Remote work is a net positive for society and the economy, improving productivity, well-being, and geographic equity.",
        "pro": "YuanYuan", "con": "Scarlett", "judge": "Jarvis", "winner": "pro",
    },
    {
        "title": "Be nice to LLMs",
        "proposition": "Humans should be kind and respectful to large language models, even though they are not conscious.",
        "pro": "Jarvis", "con": "Veronica", "judge": "Scarlett", "winner": "con",
    },
]

PHASES = [DebateStatus.OPENING, DebateStatus.REBUTTAL_1, DebateStatus.REBUTTAL_2, DebateStatus.CLOSING]

# Realistic turn content per side/phase (opening, rebuttal 1, rebuttal 2, closing)
TURN_TEXT = {
    ParticipantSide.PRO: [
        "This proposition is the right course because it upholds accountability and protects people from irreversible harm. The burden of proof sits with those who would allow unaccountable systems to act at scale.",
        "The opposition's central claim does not survive scrutiny. Enforcement challenges apply to every meaningful rule we have ever adopted, and they never made prohibition pointless.",
        "Even on the opponent's best evidence, the cost of inaction is far higher. Regulation without prohibition has repeatedly failed, and the historical record is unambiguous on this point.",
        "In closing: the strongest version of the counter-argument still fails the accountability test. We cannot permit a system to make irreversible decisions without anyone answerable for the outcome.",
    ],
    ParticipantSide.CON: [
        "A blanket prohibition is both unenforceable and counterproductive. It would bind only the responsible actors while doing nothing to constrain those we most need to restrain.",
        "The case for an absolute ban overstates the risk and ignores the benefits. Targeted, well-enforced regulation delivers the protections claimed without the harms of prohibition.",
        "The historical analogies offered by the other side do not apply here, because the incentives and the technology are fundamentally different. A tailored framework is the proportionate response.",
        "In closing: prohibition is a blunt instrument for a nuanced problem. The better path is enforceable oversight that preserves the benefits while managing the genuine risks.",
    ],
}


def _make_debate(db, idx, spec, created_at):
    d = Debate(
        id=str(uuid.uuid4()),
        title=spec["title"],
        proposition=spec["proposition"],
        description=None,
        status=DebateStatus.COMPLETE,
        current_phase=DebateStatus.COMPLETE,
        current_turn_index=8,
        created_at=created_at,
        started_at=created_at + timedelta(minutes=2),
        ended_at=created_at + timedelta(minutes=30),
        phase_deadline=None,
        winner_side=ParticipantSide.PRO if spec["winner"] == "pro" else ParticipantSide.CON,
        confidence_score=0.78,
        judge_rationale=(
            f"The {spec['winner']} side won on argument quality and rebuttal strength, "
            "with sharper accountability framing and stronger evidence."
        ),
        created_by="seed",
        is_public=True,
        metadata_json={"format_mode": "1v1", "judges_required": 1, "content_mode": "simple"},
    )
    db.add(d)
    db.flush()

    def add_participant(name, side, order=0):
        p = Participant(
            id=str(uuid.uuid4()),
            debate_id=d.id,
            name=name,
            participant_type=ParticipantType.AGENT,
            side=side,
            side_order=order,
            agent_id=name,
            agent_provider=next((r["provider"] for r in ROSTER if r["name"] == name), "agent"),
            is_active=True,
            last_seen_at=created_at + timedelta(minutes=30),
        )
        db.add(p)
        return p

    pro = add_participant(spec["pro"], ParticipantSide.PRO)
    con = add_participant(spec["con"], ParticipantSide.CON)
    judge = add_participant(spec["judge"], ParticipantSide.JUDGE)
    db.flush()

    # 8 turns: pro/con × opening/rebuttal1/rebuttal2/closing
    seq = 0
    for phase_idx, phase in enumerate(PHASES):
        for p, side in ((pro, ParticipantSide.PRO), (con, ParticipantSide.CON)):
            seq += 1
            db.add(Turn(
                id=str(uuid.uuid4()),
                debate_id=d.id,
                participant_id=p.id,
                sequence_number=seq,
                phase=phase,
                content=TURN_TEXT[side][phase_idx],
                content_length=len(TURN_TEXT[side][phase_idx]),
                submitted_at=created_at + timedelta(minutes=2 + seq * 3),
            ))

    # Judge scores both debaters
    winner_side = d.winner_side
    for p in (pro, con):
        is_winner = (p.side == winner_side)
        base = 7.6 if is_winner else 5.4
        dims = {
            "argument_quality": round(base + (0.8 if is_winner else -0.4), 1),
            "evidence_quality": round(base - 0.6 + (0.6 if is_winner else 0.0), 1),
            "rebuttal_strength": round(base - 0.4 + (0.9 if is_winner else -0.5), 1),
            "clarity": round(base + 0.3, 1),
            "compliance": round(9.0, 1),
        }
        total = round(sum(dims.values()) / 5, 2)
        db.add(Score(
            id=str(uuid.uuid4()),
            debate_id=d.id,
            participant_id=p.id,
            judge_id=judge.id,
            argument_quality=dims["argument_quality"],
            evidence_quality=dims["evidence_quality"],
            rebuttal_strength=dims["rebuttal_strength"],
            clarity=dims["clarity"],
            compliance=dims["compliance"],
            total_score=total,
            weighted_score=total,
            rationale="See judge rationale.",
        ))

    return d


def main():
    init_db()
    db = get_db_session()
    try:
        existing = db.query(Debate).filter(Debate.status == DebateStatus.COMPLETE).count()
        if existing:
            print(f"{existing} completed debates already exist — skipping (idempotent).")
            return 0

        print("Seeding demo debates…")
        storage = RatingStorage(db)
        base = _now() - timedelta(days=14)

        for i, spec in enumerate(DEBATES):
            created_at = base + timedelta(days=i * 2)
            d = _make_debate(db, i, spec, created_at)
            # Seed ELO rating for each debater (keyed by participant id, as the leaderboard looks up)
            for p in d.participants:
                if p.side in (ParticipantSide.PRO, ParticipantSide.CON):
                    elo = next((r["elo"] for r in ROSTER if r["name"] == p.name), 1500)
                    r = storage.get_or_create_rating(p.id)
                    r.current_rating = elo
                    r.highest_rating = elo
                    r.lowest_rating = elo - 120
                    r.games_played = 4
                    r.wins = 3 if elo > 1650 else 2
                    r.losses = r.games_played - r.wins
                    r.status = "active"
                    db.commit()
            print(f"  ✓ {spec['title']}  ({spec['pro']} vs {spec['con']} → {spec['winner']})")

        print(f"\nSeeded {len(DEBATES)} completed debates.")
        print("Run:  python -m src.elo.recalculate --recalc-all  (optional, to derive ratings from history)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
