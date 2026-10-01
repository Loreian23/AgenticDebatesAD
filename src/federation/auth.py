"""Federation API key authentication.

Provides secure API key generation and validation for external agents
connecting to the Agent Debate platform via the Federation SDK.

Security features:
- HMAC-SHA256 based API keys (not just random strings)
- Key prefix for identification (not secrecy)
- Rolling keys with grace period for rotation
- Rate limiting per key
- Audit logging for all authentication events
"""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from src.database import get_db_session, Base
from sqlalchemy import Column, String, Integer, DateTime, Boolean, JSON, Text, Index


class FederationKeyStatus(str, Enum):
    """Status of a federation API key."""
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class FederationAPIKey(Base):
    """API key for external federation agents.
    
    Security design:
    - Key hash stored (never plaintext)
    - Key prefix stored separately for identification
    - Rolling keys supported (old key valid during grace period)
    """
    __tablename__ = "federation_api_keys"
    
    id = Column(String(36), primary_key=True)
    agent_id = Column(String(255), nullable=False, index=True)
    org_id = Column(String(255), nullable=True)
    
    # Key identification (not secret)
    key_prefix = Column(String(16), nullable=False)  # e.g., "adb_fed_abc123"
    key_hash = Column(String(64), nullable=False)  # HMAC-SHA256 of full key
    
    # Key metadata
    name = Column(String(255), nullable=True)  # Human-readable name
    description = Column(Text, nullable=True)
    
    # Status
    status = Column(String(20), default=FederationKeyStatus.ACTIVE.value)
    
    # Rolling key support
    previous_key_hash = Column(String(64), nullable=True)  # For key rotation
    rotation_deadline = Column(DateTime, nullable=True)  # Grace period end
    
    # Security
    rate_limit_per_minute = Column(Integer, default=60)
    last_used_at = Column(DateTime, nullable=True)
    use_count = Column(Integer, default=0)
    failed_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    
    # Expiry
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Audit
    created_by = Column(String(255), nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String(255), nullable=True)
    
    __table_args__ = (
        Index('idx_fed_key_agent', 'agent_id'),
        Index('idx_fed_key_prefix', 'key_prefix'),
    )


class FederationAuditLog(Base):
    """Audit log for federation authentication events."""
    __tablename__ = "federation_audit_log"
    
    id = Column(String(36), primary_key=True)
    key_id = Column(String(36), nullable=True)
    agent_id = Column(String(255), nullable=True)
    event_type = Column(String(50), nullable=False)  # auth_success, auth_failure, key_created, key_revoked
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    request_path = Column(String(500), nullable=True)
    details = Column(JSON, default=dict)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_fed_audit_agent', 'agent_id'),
        Index('idx_fed_audit_type', 'event_type'),
        Index('idx_fed_audit_time', 'timestamp'),
    )


class FederationTokenError(Exception):
    """Error with federation token operations."""
    pass


@dataclass
class AuthResult:
    """Result of an authentication attempt."""
    success: bool
    agent_id: Optional[str] = None
    org_id: Optional[str] = None
    key_id: Optional[str] = None
    error: Optional[str] = None
    rate_limit_remaining: int = 60
    retry_after: Optional[int] = None  # Seconds until lock expires


class FederationAuth:
    """Manage federation API keys for external agents.
    
    Usage:
        auth = FederationAuth()
        
        # Create a new API key for an external agent
        prefix, full_key = auth.create_api_key(
            agent_id="agent_veronica_v1",
            org_id="openclaw",
            name="Veronica Production"
        )
        # Store full_key securely - shown only once!
        
        # Validate an incoming request
        result = auth.validate_request(api_key="adb_fed_xxx...", request_path="/api/federation/join")
        if result.success:
            print(f"Authenticated: {result.agent_id}")
    """
    
    # Key format: {prefix}_{secret} where prefix is stored, secret is hashed
    KEY_PREFIX = "adb_fed"
    SECRET_BYTES = 32  # 256 bits
    
    # Security settings
    MAX_FAILED_ATTEMPTS = 5
    LOCKOUT_DURATION_MINUTES = 15
    KEY_ROTATION_GRACE_HOURS = 24
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or get_db_session()
    
    def _generate_secret(self) -> str:
        """Generate a cryptographically secure secret."""
        return secrets.token_hex(self.SECRET_BYTES)
    
    def _hash_key(self, key: str) -> str:
        """Create HMAC-SHA256 hash of key."""
        return hashlib.sha256(key.encode()).hexdigest()
    
    def _verify_key(self, key: str, stored_hash: str, previous_hash: Optional[str] = None) -> bool:
        """Verify key against stored hash (supports key rotation)."""
        key_hash = self._hash_key(key)
        
        # Check current key
        if hmac.compare_digest(key_hash, stored_hash):
            return True
        
        # Check previous key (during rotation grace period)
        if previous_hash and hmac.compare_digest(key_hash, previous_hash):
            return True
        
        return False
    
    def create_api_key(
        self,
        agent_id: str,
        org_id: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        expires_days: Optional[int] = 90,
        rate_limit: int = 60,
        created_by: str = "system",
    ) -> Tuple[str, str]:
        """Create a new API key for an external agent.
        
        Returns:
            (key_prefix, full_key) - full_key must be stored securely, shown only once!
        
        Example:
            prefix, key = auth.create_api_key(
                agent_id="agent_xyz",
                org_id="acme_corp",
                name="Production Key"
            )
            # Store key in external agent's secure config
        """
        import uuid
        
        # Generate key
        secret = self._generate_secret()
        prefix = f"{self.KEY_PREFIX}_{secrets.token_hex(8)}"
        full_key = f"{prefix}_{secret}"
        key_hash = self._hash_key(full_key)
        
        # Calculate expiry
        expires_at = None
        if expires_days:
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expires_days)
        
        # Create record
        key_record = FederationAPIKey(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            org_id=org_id,
            key_prefix=prefix,
            key_hash=key_hash,
            name=name,
            description=description,
            expires_at=expires_at,
            rate_limit_per_minute=rate_limit,
            created_by=created_by,
        )
        
        self.db.add(key_record)
        self.db.commit()
        
        # Audit log
        self._audit("key_created", key_record.id, agent_id, details={
            "org_id": org_id,
            "name": name,
            "expires_at": expires_at.isoformat() if expires_at else None,
        })
        
        return prefix, full_key
    
    def validate_request(
        self,
        api_key: str,
        request_path: str = "/",
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuthResult:
        """Validate an API key from an incoming request.
        
        Returns AuthResult with success=True if valid, error details if not.
        """
        import uuid
        
        # Parse key
        if not api_key or "_" not in api_key:
            return AuthResult(success=False, error="Invalid API key format")
        
        parts = api_key.split("_", 3)
        if len(parts) < 4 or parts[0] != "adb" or parts[1] != "fed":
            return AuthResult(success=False, error="Invalid API key format")
        
        prefix = "_".join(parts[:3])
        secret = "_".join(parts[3:])
        
        # Look up key record
        key_record = self.db.query(FederationAPIKey).filter(
            FederationAPIKey.key_prefix == prefix
        ).first()
        
        if not key_record:
            self._audit("auth_failure", None, None, ip_address, request_path, 
                       {"reason": "unknown_key_prefix"})
            return AuthResult(success=False, error="Invalid API key")
        
        # Check status
        if key_record.status == FederationKeyStatus.REVOKED.value:
            self._audit("auth_failure", key_record.id, key_record.agent_id, ip_address, request_path,
                       {"reason": "key_revoked"})
            return AuthResult(success=False, error="API key has been revoked")
        
        if key_record.status == FederationKeyStatus.EXPIRED.value:
            self._audit("auth_failure", key_record.id, key_record.agent_id, ip_address, request_path,
                       {"reason": "key_expired"})
            return AuthResult(success=False, error="API key has expired")
        
        # Check expiry
        if key_record.expires_at and datetime.now(timezone.utc).replace(tzinfo=None) > key_record.expires_at:
            key_record.status = FederationKeyStatus.EXPIRED.value
            self.db.commit()
            self._audit("auth_failure", key_record.id, key_record.agent_id, ip_address, request_path,
                       {"reason": "key_expired"})
            return AuthResult(success=False, error="API key has expired")
        
        # Check lockout
        if key_record.locked_until and datetime.now(timezone.utc).replace(tzinfo=None) < key_record.locked_until:
            retry_after = int((key_record.locked_until - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds())
            self._audit("auth_failure", key_record.id, key_record.agent_id, ip_address, request_path,
                       {"reason": "locked_out", "retry_after": retry_after})
            return AuthResult(
                success=False, 
                error="Too many failed attempts",
                retry_after=retry_after
            )
        
        # Verify key
        if not self._verify_key(api_key, key_record.key_hash, key_record.previous_key_hash):
            # Increment failed attempts
            key_record.failed_attempts += 1
            
            if key_record.failed_attempts >= self.MAX_FAILED_ATTEMPTS:
                key_record.locked_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=self.LOCKOUT_DURATION_MINUTES)
                self._audit("auth_failure", key_record.id, key_record.agent_id, ip_address, request_path,
                           {"reason": "max_attempts", "locked_until": key_record.locked_until.isoformat()})
            else:
                self._audit("auth_failure", key_record.id, key_record.agent_id, ip_address, request_path,
                           {"reason": "invalid_key", "attempts": key_record.failed_attempts})
            
            self.db.commit()
            return AuthResult(
                success=False, 
                error="Invalid API key",
                retry_after=retry_after if key_record.locked_until else None
            )
        
        # Success - update stats
        key_record.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        key_record.use_count += 1
        key_record.failed_attempts = 0
        self.db.commit()
        
        self._audit("auth_success", key_record.id, key_record.agent_id, ip_address, request_path)
        
        return AuthResult(
            success=True,
            agent_id=key_record.agent_id,
            org_id=key_record.org_id,
            key_id=key_record.id,
            rate_limit_remaining=key_record.rate_limit_per_minute - (key_record.use_count % key_record.rate_limit_per_minute),
        )
    
    def rotate_key(
        self,
        key_id: str,
        rotated_by: str = "system",
        grace_hours: int = 24,
    ) -> Tuple[str, str]:
        """Rotate an API key, allowing old key during grace period.
        
        Returns:
            (new_prefix, new_full_key)
        """
        key_record = self.db.query(FederationAPIKey).filter(
            FederationAPIKey.id == key_id
        ).first()
        
        if not key_record:
            raise FederationTokenError(f"Key not found: {key_id}")
        
        if key_record.status != FederationKeyStatus.ACTIVE.value:
            raise FederationTokenError(f"Cannot rotate {key_record.status} key")
        
        # Store old key hash for grace period
        key_record.previous_key_hash = key_record.key_hash
        key_record.rotation_deadline = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=grace_hours)
        
        # Generate new key
        secret = self._generate_secret()
        prefix = f"{self.KEY_PREFIX}_{secrets.token_hex(8)}"
        full_key = f"{prefix}_{secret}"
        key_record.key_prefix = prefix
        key_record.key_hash = self._hash_key(full_key)
        
        self.db.commit()
        
        self._audit("key_rotated", key_record.id, key_record.agent_id, details={
            "rotated_by": rotated_by,
            "grace_hours": grace_hours,
        })
        
        return prefix, full_key
    
    def revoke_key(self, key_id: str, revoked_by: str = "system") -> bool:
        """Revoke an API key immediately."""
        key_record = self.db.query(FederationAPIKey).filter(
            FederationAPIKey.id == key_id
        ).first()
        
        if not key_record:
            return False
        
        key_record.status = FederationKeyStatus.REVOKED.value
        key_record.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        key_record.revoked_by = revoked_by
        
        self.db.commit()
        
        self._audit("key_revoked", key_record.id, key_record.agent_id, details={
            "revoked_by": revoked_by,
        })
        
        return True
    
    def get_key_info(self, key_id: str) -> Optional[Dict[str, Any]]:
        """Get API key info by key ID (without the actual key)."""
        key_record = self.db.query(FederationAPIKey).filter(
            FederationAPIKey.id == key_id
        ).first()
        
        if not key_record:
            return None
        
        return self._key_record_to_dict(key_record)
    
    def get_key_info_by_prefix(self, key_prefix: str) -> Optional[Dict[str, Any]]:
        """Get API key info by key prefix (without the actual key)."""
        key_record = self.db.query(FederationAPIKey).filter(
            FederationAPIKey.key_prefix == key_prefix
        ).first()
        
        if not key_record:
            return None
        
        return self._key_record_to_dict(key_record)
    
    def _key_record_to_dict(self, key_record: FederationAPIKey) -> Dict[str, Any]:
        """Convert key record to dictionary."""
        return {
            "id": key_record.id,
            "agent_id": key_record.agent_id,
            "org_id": key_record.org_id,
            "name": key_record.name,
            "status": key_record.status,
            "expires_at": key_record.expires_at.isoformat() if key_record.expires_at else None,
            "created_at": key_record.created_at.isoformat() if key_record.created_at else None,
            "last_used_at": key_record.last_used_at.isoformat() if key_record.last_used_at else None,
            "use_count": key_record.use_count,
            "rate_limit_per_minute": key_record.rate_limit_per_minute,
        }
    
    def list_keys_for_agent(self, agent_id: str) -> list:
        """List all API keys for an agent."""
        keys = self.db.query(FederationAPIKey).filter(
            FederationAPIKey.agent_id == agent_id
        ).all()
        
        return [
            {
                "id": k.id,
                "key_prefix": k.key_prefix,
                "name": k.name,
                "status": k.status,
                "expires_at": k.expires_at.isoformat() if k.expires_at else None,
                "created_at": k.created_at.isoformat() if k.created_at else None,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "use_count": k.use_count,
            }
            for k in keys
        ]
    
    def _audit(
        self,
        event_type: str,
        key_id: Optional[str],
        agent_id: Optional[str],
        ip_address: Optional[str] = None,
        request_path: Optional[str] = None,
        details: Optional[Dict] = None,
        user_agent: Optional[str] = None,
    ):
        """Create an audit log entry."""
        import uuid
        
        log = FederationAuditLog(
            id=str(uuid.uuid4()),
            key_id=key_id,
            agent_id=agent_id,
            event_type=event_type,
            ip_address=ip_address,
            request_path=request_path,
            user_agent=user_agent,
            details=details or {},
        )
        
        self.db.add(log)
        self.db.commit()
    
    def cleanup_expired_keys(self) -> int:
        """Mark expired keys and return count cleaned."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        expired = self.db.query(FederationAPIKey).filter(
            FederationAPIKey.status == FederationKeyStatus.ACTIVE.value,
            FederationAPIKey.expires_at < now
        ).all()
        
        count = 0
        for key in expired:
            key.status = FederationKeyStatus.EXPIRED.value
            count += 1
        
        # Also clean up past rotation deadlines
        past_grace = self.db.query(FederationAPIKey).filter(
            FederationAPIKey.rotation_deadline < now,
            FederationAPIKey.previous_key_hash.isnot(None)
        ).all()
        
        for key in past_grace:
            key.previous_key_hash = None  # Remove old key access
        
        self.db.commit()
        return count
