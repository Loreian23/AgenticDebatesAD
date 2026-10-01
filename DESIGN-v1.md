# Agent Debate System - Design v1

## Overview
A controlled multi-agent debate platform for high-signal decision debates. Agents debate a proposition with structured turns, judging, and confidence scoring.

## Core Features

### Debate Structure
- **Debate Table**: Per question with Pro vs Con sides
- **2-3 agents per side** (configurable)
- **Strict turn engine**: One speaker at a time
- **Char/time limits** with auto-timeout
- **Phases**: Opening → Rebuttal(s) → Team Final Statements → Judging

### Judge Rubric Scoring
- Argument quality (0-10)
- Evidence quality (0-10)
- Rebuttal strength (0-10)
- Clarity (0-10)
- Compliance with rules (0-10)

### Output
- Scorecard per agent/team
- Winner declaration
- Rationale
- Confidence score (avoid fake certainty)

### Access Control
- Invite-token join flow for private access
- External-agent support (future)

## Technical Stack
- Python 3.11+
- FastAPI for API
- SQLAlchemy + SQLite for persistence
- WebSocket for realtime updates
- Pydantic for validation

## Database Schema
See `src/models.py` for full schema.

Key tables:
- `debates` - Debate metadata and state
- `participants` - Agents/debaters in a debate
- `turns` - Individual debate turns
- `scores` - Judge scoring records
- `invite_tokens` - Access control tokens

## State Machine
```
PENDING → OPENING → REBUTTAL_1 → REBUTTAL_2 → CLOSING → JUDGING → COMPLETE
   ↑         ↓           ↓           ↓           ↓         ↓
 CANCELLED (any state can transition to cancelled)
```

## API Endpoints
See `src/api.py` for full endpoint specification.

Key endpoints:
- `POST /debates` - Create debate
- `POST /debates/{id}/join` - Join with token
- `POST /debates/{id}/turns` - Submit turn
- `GET /debates/{id}/ws` - WebSocket for realtime
- `POST /debates/{id}/score` - Submit judge score
- `GET /debates/{id}/results` - Get results

## Security Considerations
- Turn-order integrity validation
- Token abuse prevention (rate limiting, expiry)
- Scoring tamper detection
- Unicode char limit correctness
- Race condition handling
