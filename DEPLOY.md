# Agent Debate System - Deployment Guide

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run database migrations
alembic upgrade head

# 3. Start the server
python main.py
```

## Verification Commands

```bash
# Run all tests
python -m pytest tests/ -v

# Quick test run
python -m pytest tests/ -q

# Syntax validation
python3 -m py_compile src/models.py src/api.py src/state_machine.py src/invite_tokens.py
```

## Server Endpoints

- `POST /debates` - Create new debate (host only)
- `POST /debates/{id}/start` - Start debate (host only, requires PRO+CON+JUDGE)
- `POST /debates/{id}/finalize` - Finalize and score (host only, all scores required)
- `POST /invite-tokens` - Create invite token
- `POST /join` - Join debate with token
- `POST /scores` - Submit judge score

## Environment Variables

- `DATABASE_URL` - PostgreSQL connection string
- `SECRET_KEY` - JWT signing key
