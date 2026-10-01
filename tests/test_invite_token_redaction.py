from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api import create_invite_token, list_invite_tokens
from src.database import Base
from src.models import Debate, ParticipantSide, ParticipantType
from src.schemas import InviteTokenCreate


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return TestingSessionLocal()


def _make_debate(db):
    debate = Debate(
        title="Token redaction",
        proposition="Invite list responses should not expose reusable secrets",
        created_by="test-host",
    )
    db.add(debate)
    db.commit()
    db.refresh(debate)
    return debate


def test_list_invite_tokens_redacts_full_token_after_creation():
    db = _make_db()
    try:
        debate = _make_debate(db)
        debate_id = str(debate.id)
        created = create_invite_token(
            debate_id,
            InviteTokenCreate(
                side=ParticipantSide.PRO,
                participant_type=ParticipantType.AGENT,
                max_uses=1,
                expires_hours=24,
                created_by="test-host",
            ),
            db,
        )

        assert created["token"]

        listed = list_invite_tokens(debate_id, db)

        assert len(listed) == 1
        assert "token" not in listed[0]
        assert listed[0]["token_preview"] == created["token_preview"]
    finally:
        db.close()
