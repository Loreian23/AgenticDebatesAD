"""Invite token management with security features."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from src.models import InviteToken, InviteTokenStatus, Participant, ParticipantSide, ParticipantType
from src.database import get_db_session


class InviteTokenError(Exception):
    """Error with invite token operation."""
    pass


class InviteTokenManager:
    """Manage invite tokens with rate limiting and abuse prevention."""
    
    # BLOCKER FIX #2: Rate limiting - max attempts per IP per hour
    MAX_ATTEMPTS_PER_IP = 10
    ATTEMPT_WINDOW_HOURS = 1
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or get_db_session()
    
    def _hash_token(self, token: str) -> str:
        """Create SHA-256 hash of token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()
    
    def _generate_token(self) -> str:
        """Generate a secure random token."""
        # 32 bytes = 64 hex characters
        return secrets.token_hex(32)
    
    def create_token(
        self,
        debate_id: str,
        side: ParticipantSide,
        participant_type: ParticipantType = ParticipantType.AGENT,
        max_uses: int = 1,
        expires_hours: Optional[int] = 168,  # 1 week default
        created_by: str = "system",
    ) -> Tuple[str, str]:
        """
        Create a new invite token.
        
        Returns:
            (token, token_preview) - Token is shown once, preview for display
        """
        # Generate token
        token = self._generate_token()
        token_hash = self._hash_token(token)
        token_preview = f"{token[:8]}...{token[-6:]}" if len(token) >= 16 else token
        
        # Calculate expiry
        expires_at = None
        if expires_hours:
            expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=expires_hours)
        
        # Create record
        invite = InviteToken(
            debate_id=debate_id,
            token_hash=token_hash,
            token_preview=token_preview,
            side=side,
            participant_type=participant_type,
            max_uses=max_uses,
            expires_at=expires_at,
            created_by=created_by,
        )
        
        self.db.add(invite)
        self.db.commit()
        
        return token, token_preview
    
    def _get_token_record(self, token: str) -> Optional[InviteToken]:
        """Get token record by full token value."""
        token_hash = self._hash_token(token)
        return self.db.query(InviteToken).filter(InviteToken.token_hash == token_hash).first()
    
    def _check_rate_limit(self, ip_address: str) -> bool:
        """Check if IP has exceeded rate limit."""
        # Get recent attempts from this IP
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=self.ATTEMPT_WINDOW_HOURS)
        
        # Count attempts in audit log
        from src.models import AuditLog
        recent_attempts = self.db.query(AuditLog).filter(
            AuditLog.event_type == "token_attempt",
            AuditLog.timestamp >= cutoff,
        ).all()

        recent_attempts = sum(
            1
            for attempt in recent_attempts
            if (attempt.event_data or {}).get("ip_address") == ip_address
        )

        return recent_attempts < self.MAX_ATTEMPTS_PER_IP
    
    def validate_token(
        self, 
        token: str, 
        ip_address: Optional[str] = None
    ) -> Tuple[bool, Optional[str], Optional[InviteToken]]:
        """
        Validate an invite token.
        
        Returns:
            (is_valid, error_message, token_record)
        """
        # Check rate limit if IP provided
        if ip_address and not self._check_rate_limit(ip_address):
            return False, "Rate limit exceeded. Try again later.", None
        
        # Get token record
        token_record = self._get_token_record(token)
        
        if not token_record:
            # Log failed attempt
            self._log_attempt(token, ip_address, False, "Invalid token")
            return False, "Invalid invite token", None
        
        # Check status
        if token_record.status == InviteTokenStatus.REVOKED:
            self._log_attempt(token, ip_address, False, "Token revoked", token_record.id)
            return False, "This invite token has been revoked", None
        
        if token_record.status == InviteTokenStatus.EXPIRED:
            self._log_attempt(token, ip_address, False, "Token expired", token_record.id)
            return False, "This invite token has expired", None
        
        if token_record.status == InviteTokenStatus.USED:
            if token_record.used_count >= token_record.max_uses:
                self._log_attempt(token, ip_address, False, "Token exhausted", token_record.id)
                return False, "This invite token has already been used", None
        
        # Check expiry
        if token_record.expires_at and datetime.now(timezone.utc).replace(tzinfo=None) > token_record.expires_at:
            token_record.status = InviteTokenStatus.EXPIRED
            self.db.commit()
            self._log_attempt(token, ip_address, False, "Token expired", token_record.id)
            return False, "This invite token has expired", None
        
        # Valid
        self._log_attempt(token, ip_address, True, "Valid", token_record.id)
        return True, None, token_record
    
    def _log_attempt(
        self, 
        token: str, 
        ip_address: Optional[str], 
        success: bool, 
        reason: str,
        token_id: Optional[str] = None
    ):
        """Log token attempt for audit."""
        from src.models import AuditLog
        
        log = AuditLog(
            debate_id=None,  # Will be set if token found
            event_type="token_attempt",
            event_data={
                "token_preview": token[:8] if len(token) >= 8 else token,
                "ip_address": ip_address,
                "success": success,
                "reason": reason,
                "token_id": token_id,
            },
            actor_type="user",
            actor_id=ip_address,
        )
        
        self.db.add(log)
        self.db.commit()
    
    def use_token(
        self,
        token: str,
        participant_name: str,
        participant_type: Optional[ParticipantType] = None,
        ip_address: Optional[str] = None,
    ) -> Participant:
        """
        Use an invite token to join a debate.
        
        Returns:
            Created Participant
        
        Raises:
            ValueError: If token is invalid
        """
        # Validate
        is_valid, error, token_record = self.validate_token(token, ip_address)
        if not is_valid:
            raise ValueError(error)
        
        # Determine participant type
        if participant_type is None:
            participant_type = token_record.participant_type
        
        # Create participant
        participant = Participant(
            debate_id=token_record.debate_id,
            name=participant_name,
            participant_type=participant_type,
            side=token_record.side,
            invite_token_id=token_record.id,
        )
        
        self.db.add(participant)
        
        # Update token
        token_record.used_count += 1
        token_record.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Update status if exhausted
        if token_record.used_count >= token_record.max_uses:
            token_record.status = InviteTokenStatus.USED
        
        self.db.commit()
        self.db.refresh(participant)
        
        return participant
    
    def revoke_token(self, token_id: str, revoked_by: str) -> bool:
        """Revoke an invite token."""
        token = self.db.query(InviteToken).filter(InviteToken.id == token_id).first()
        
        if not token:
            return False
        
        token.status = InviteTokenStatus.REVOKED
        
        # Log
        from src.models import AuditLog
        log = AuditLog(
            debate_id=token.debate_id,
            event_type="token_revoked",
            event_data={
                "token_id": token_id,
                "revoked_by": revoked_by,
            },
            actor_type="user",
            actor_id=revoked_by,
        )
        self.db.add(log)
        self.db.commit()
        
        return True
    
    def cleanup_expired_tokens(self) -> int:
        """Mark expired tokens and return count cleaned."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        expired = self.db.query(InviteToken).filter(
            InviteToken.status == InviteTokenStatus.ACTIVE,
            InviteToken.expires_at < now
        ).all()
        
        count = 0
        for token in expired:
            token.status = InviteTokenStatus.EXPIRED
            count += 1
        
        self.db.commit()
        return count
