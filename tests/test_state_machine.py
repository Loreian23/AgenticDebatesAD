"""Tests for debate state machine."""

import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, get_db_session
from src.models import Debate, Participant, Turn, Score, DebateStatus, ParticipantSide, ParticipantType
from src.state_machine import DebateStateMachine, InvalidTurnError, StateTransitionError


@pytest.fixture
def db_session():
    """Create a test database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    # Create a test debate
    debate = Debate(
        id="test-debate-1",
        title="Test Debate",
        proposition="Test proposition",
        created_by="test",
        max_turn_length=1000,
    )
    db.add(debate)
    
    # Add participants - need PRO, CON, and JUDGE for debate to start
    pro1 = Participant(id="pro-1", debate_id="test-debate-1", name="Pro Agent 1", side=ParticipantSide.PRO, side_order=0)
    pro2 = Participant(id="pro-2", debate_id="test-debate-1", name="Pro Agent 2", side=ParticipantSide.PRO, side_order=1)
    con1 = Participant(id="con-1", debate_id="test-debate-1", name="Con Agent 1", side=ParticipantSide.CON, side_order=0)
    con2 = Participant(id="con-2", debate_id="test-debate-1", name="Con Agent 2", side=ParticipantSide.CON, side_order=1)
    judge1 = Participant(id="judge-1", debate_id="test-debate-1", name="Judge 1", side=ParticipantSide.JUDGE, side_order=0)
    
    db.add_all([pro1, pro2, con1, con2, judge1])
    db.commit()
    
    yield db
    
    db.close()


def test_build_turn_order(db_session):
    """Test turn order generation."""
    sm = DebateStateMachine("test-debate-1", db_session)
    turn_order = sm.build_turn_order()
    
    assert len(turn_order) > 0
    
    # Check opening statements - all participants
    opening_turns = [t for t in turn_order if t["phase"] == DebateStatus.OPENING]
    assert len(opening_turns) == 4  # 2 pro + 2 con
    
    # Check sequence numbers are unique and sequential
    sequences = [t["sequence_number"] for t in turn_order]
    assert sequences == list(range(1, len(turn_order) + 1))


def test_start_debate(db_session):
    """Test debate start transition."""
    sm = DebateStateMachine("test-debate-1", db_session)
    debate = sm.start_debate()
    
    assert debate.status == DebateStatus.OPENING
    assert debate.current_phase == DebateStatus.OPENING
    assert debate.started_at is not None
    assert debate.phase_deadline is not None


def test_start_debate_already_started(db_session):
    """Test cannot start already started debate."""
    sm = DebateStateMachine("test-debate-1", db_session)
    sm.start_debate()
    
    with pytest.raises(StateTransitionError):
        sm.start_debate()


def test_submit_turn_not_your_turn(db_session):
    """Test cannot submit when not your turn."""
    sm = DebateStateMachine("test-debate-1", db_session)
    sm.start_debate()
    
    # Try to submit with wrong participant
    with pytest.raises(InvalidTurnError):
        sm.submit_turn("con-1", "My argument")


def test_submit_turn_success(db_session):
    """Test successful turn submission."""
    sm = DebateStateMachine("test-debate-1", db_session)
    sm.start_debate()
    
    # Get current turn participant
    current = sm.get_current_turn()
    assert current is not None
    
    # Submit turn
    turn = sm.submit_turn(current["participant_id"], "My opening statement")
    
    assert turn.content == "My opening statement"
    assert turn.sequence_number == current["sequence_number"]
    assert turn.phase == DebateStatus.OPENING


def test_submit_turn_char_limit(db_session):
    """Test character limit enforcement."""
    sm = DebateStateMachine("test-debate-1", db_session)
    sm.start_debate()
    
    current = sm.get_current_turn()
    
    # Submit content that exceeds limit
    long_content = "x" * 2000  # Exceeds default 1000
    with pytest.raises(InvalidTurnError) as exc_info:
        sm.submit_turn(current["participant_id"], long_content)

    assert "Content exceeds maximum length" in str(exc_info.value)


def test_unicode_char_counting(db_session):
    """Test Unicode character counting (not bytes)."""
    sm = DebateStateMachine("test-debate-1", db_session)
    sm.start_debate()
    
    current = sm.get_current_turn()
    
    # Unicode content: 10 characters, but more bytes
    unicode_content = "こんにちは世界"  # 7 Japanese characters
    turn = sm.submit_turn(current["participant_id"], unicode_content)
    
    assert turn.content_length == 7  # Character count, not byte count


def test_cancel_debate(db_session):
    """Test debate cancellation."""
    sm = DebateStateMachine("test-debate-1", db_session)
    sm.start_debate()
    
    debate = sm.cancel_debate("Test cancellation")
    
    assert debate.status == DebateStatus.CANCELLED
    assert debate.ended_at is not None


def test_get_debate_state(db_session):
    """Test getting debate state."""
    sm = DebateStateMachine("test-debate-1", db_session)
    sm.start_debate()
    
    state = sm.get_debate_state()
    
    assert state["debate_id"] == "test-debate-1"
    assert state["status"] == "opening"
    assert "turn_order" in state
    assert "current_turn" in state


def test_phase_advancement(db_session):
    """Test automatic phase advancement."""
    sm = DebateStateMachine("test-debate-1", db_session)
    debate = sm.start_debate()
    
    # Submit all opening statements
    for _ in range(4):  # 4 participants
        current = sm.get_current_turn()
        if current:
            sm.submit_turn(current["participant_id"], "Opening statement")
    
    # Should now be in rebuttal phase
    db_session.refresh(debate)
    assert debate.current_phase == DebateStatus.REBUTTAL_1


class TestTurnTimeoutHandler:
    """Tests for turn timeout handling."""
    
    def test_timeout_marked_correctly(self, db_session):
        """Test timeouts are marked correctly."""
        from src.state_machine import TurnTimeoutHandler, DebateStateMachine
        
        # Start the debate properly first
        sm = DebateStateMachine("test-debate-1", db_session)
        sm.start_debate()
        
        handler = TurnTimeoutHandler(db_session)
        
        # Expire the debate deadline
        debate = db_session.query(Debate).filter(Debate.id == "test-debate-1").first()
        debate.phase_deadline = datetime.utcnow()  # Already expired
        db_session.commit()
        
        # Process timeouts
        results = handler.process_timeouts()
        
        assert len(results) > 0
        assert results[0]["debate_id"] == "test-debate-1"
