"""Integration tests for Federation SDK auth module."""

import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api import app
from src.database import Base, get_db
from src.federation import (
    generate_agent_id,
    generate_api_key,
    hash_api_key,
    register_federated_agent,
    verify_federated_agent,
    create_agent_session,
    FEDERATION_API_KEY_PREFIX,
)
from src.federation import FederationAuth, AgentRegistry


# ============== Test Database Setup ==============

SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///./test_federation.db"
engine = create_engine(SQLALCHEMY_TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope="function")
def db():
    """Create fresh database for each test."""
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    yield db
    db.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db):
    """Create test client."""
    Base.metadata.create_all(bind=engine)
    yield TestClient(app)
    Base.metadata.drop_all(bind=engine)


# ============== API Key Generation Tests ==============

class TestAPIKeyGeneration:
    """Test API key generation functions."""
    
    def test_generate_agent_id_format(self):
        """Agent IDs should start with 'agent_' and be URL-safe."""
        agent_id = generate_agent_id()
        assert agent_id.startswith("agent_")
        assert len(agent_id) > 10
        assert "_" in agent_id[6:]  # Should have hex after prefix
    
    def test_generate_api_key_format(self):
        """API keys should start with 'fdk_live_'."""
        api_key = generate_api_key()
        assert api_key.startswith(FEDERATION_API_KEY_PREFIX)
        assert len(api_key) > 20
    
    def test_generate_api_key_unique(self):
        """Each generated API key should be unique."""
        keys = [generate_api_key() for _ in range(100)]
        assert len(set(keys)) == 100
    
    def test_hash_api_key_deterministic(self):
        """Same key should always produce same hash."""
        key = generate_api_key()
        hash1 = hash_api_key(key)
        hash2 = hash_api_key(key)
        assert hash1 == hash2
    
    def test_hash_api_key_different_keys(self):
        """Different keys should produce different hashes."""
        key1 = generate_api_key()
        key2 = generate_api_key()
        assert hash_api_key(key1) != hash_api_key(key2)


# ============== Agent Registration Tests ==============

class TestAgentRegistration:
    """Test federated agent registration."""
    
    def test_register_valid_agent(self, db):
        """Should register agent with valid credentials."""
        agent = register_federated_agent(
            agent_name="TestAgent",
            provider="anthropic",
            model="claude-opus-4",
            capabilities=["opening", "rebuttal"],
            webhook_url="https://example.com/callback",
            db=db,
        )
        
        assert agent.agent_id.startswith("agent_")
        assert agent.api_key.startswith("fdk_live_")
        assert agent.agent_name == "TestAgent"
        assert agent.provider == "anthropic"
        assert agent.model == "claude-opus-4"
        assert agent.capabilities == ["opening", "rebuttal"]
        assert agent.webhook_url == "https://example.com/callback"
    
    def test_register_invalid_capability(self, db):
        """Should reject invalid capabilities."""
        with pytest.raises(Exception) as exc_info:
            register_federated_agent(
                agent_name="BadAgent",
                provider="anthropic",
                model="claude-opus-4",
                capabilities=["flying", "teleporting"],  # Invalid!
                webhook_url=None,
                db=db,
            )
        assert "Invalid capability" in str(exc_info.value.detail)
    
    def test_register_invalid_provider(self, db):
        """Should reject invalid providers."""
        with pytest.raises(Exception) as exc_info:
            register_federated_agent(
                agent_name="BadAgent",
                provider="unknown_provider",  # Invalid!
                model="claude-opus-4",
                capabilities=["opening"],
                webhook_url=None,
                db=db,
            )
        assert "Invalid provider" in str(exc_info.value.detail)
    
    def test_register_without_webhook(self, db):
        """Should allow registration without webhook URL."""
        agent = register_federated_agent(
            agent_name="SilentAgent",
            provider="openai",
            model="gpt-4",
            capabilities=["opening", "closing", "judging"],
            webhook_url=None,
            db=db,
        )
        assert agent.webhook_url is None


# ============== Agent Verification Tests ==============

class TestAgentVerification:
    """Test federated agent credential verification."""
    
    def test_verify_valid_credentials(self, db):
        """Should verify valid agent credentials."""
        # Register agent
        agent = register_federated_agent(
            agent_name="VerifyMe",
            provider="anthropic",
            model="claude-opus-4",
            capabilities=["opening"],
            webhook_url=None,
            db=db,
        )
        
        # Verify should succeed
        result = verify_federated_agent(
            api_key=agent.api_key,
            agent_id=agent.agent_id,
            db=db,
        )
        assert result is True
    
    def test_verify_invalid_api_key(self, db):
        """Should reject invalid API key."""
        agent = register_federated_agent(
            agent_name="BadKey",
            provider="anthropic",
            model="claude-opus-4",
            capabilities=["opening"],
            webhook_url=None,
            db=db,
        )
        
        with pytest.raises(Exception) as exc_info:
            verify_federated_agent(
                api_key="fdk_live_invalid_key",
                agent_id=agent.agent_id,
                db=db,
            )
        assert "AGENT_NOT_FOUND" in str(exc_info.value.detail)
    
    def test_verify_wrong_agent_id(self, db):
        """Should reject non-existent agent ID."""
        agent = register_federated_agent(
            agent_name="GhostAgent",
            provider="anthropic",
            model="claude-opus-4",
            capabilities=["opening"],
            webhook_url=None,
            db=db,
        )
        
        with pytest.raises(Exception) as exc_info:
            verify_federated_agent(
                api_key=agent.api_key,
                agent_id="agent_nonexistent",
                db=db,
            )
        assert "AGENT_NOT_FOUND" in str(exc_info.value.detail)
    
    def test_verify_wrong_prefix(self, db):
        """Should reject API keys without correct prefix."""
        with pytest.raises(Exception) as exc_info:
            verify_federated_agent(
                api_key="wrong_prefix_key",
                agent_id="agent_123",
                db=db,
            )
        assert "AGENT_NOT_FOUND" in str(exc_info.value.detail)


# ============== Session Management Tests ==============

class TestSessionManagement:
    """Test agent session management."""
    
    def test_create_session(self, db):
        """Should create valid session."""
        # Register agent first
        agent = register_federated_agent(
            agent_name="SessionAgent",
            provider="anthropic",
            model="claude-opus-4",
            capabilities=["opening"],
            webhook_url=None,
            db=db,
        )
        
        # Create session
        session = create_agent_session(
            agent_id=agent.agent_id,
            participant_id="part_123",
            debate_id="debate_456",
            side="proposition",
        )
        
        assert session.agent_id == agent.agent_id
        assert session.participant_id == "part_123"
        assert session.debate_id == "debate_456"
        assert session.side == "proposition"
        assert session.session_id is not None
    
    def test_session_id_unique(self):
        """Each session should have unique ID."""
        from src.federation import _active_sessions
        _active_sessions.clear()
        
        sessions = [
            create_agent_session("a1", "p1", "d1", "pro")
            for _ in range(10)
        ]
        session_ids = [s.session_id for s in sessions]
        assert len(set(session_ids)) == 10


# ============== Integration Test: Full Flow ==============

class TestFullFederationFlow:
    """End-to-end federation flow test."""
    
    def test_complete_registration_and_verification(self, db):
        """Test full agent lifecycle: register -> verify -> join."""
        # Step 1: Register
        agent = register_federated_agent(
            agent_name="FullFlowAgent",
            provider="openai",
            model="gpt-4",
            capabilities=["opening", "rebuttal", "closing"],
            webhook_url="https://example.com/webhook",
            db=db,
        )
        
        # Step 2: Verify
        verified = verify_federated_agent(
            api_key=agent.api_key,
            agent_id=agent.agent_id,
            db=db,
        )
        assert verified is True
        
        # Step 3: Create session
        session = create_agent_session(
            agent_id=agent.agent_id,
            participant_id="part_new",
            debate_id="debate_new",
            side="opposition",
        )
        
        assert session.agent_id == agent.agent_id
        assert session.participant_id == "part_new"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
