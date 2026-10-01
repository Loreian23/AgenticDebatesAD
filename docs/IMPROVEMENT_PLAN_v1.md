# AgentDebate Improvement Plan — v1

**Date:** 2026-07-10  
**Author:** Jarvis (on behalf of Paperclip TEA-4)  
**Repo:** https://github.com/Loreian23/AgentDebate  
**Audit scope:** Full codebase (~24,770 lines across 80+ files)

---

## Executive Summary

AgentDebate is a well-architected multi-agent debate platform with strong state machine design, comprehensive security fixes already applied, and a real task-queue system for agent workers. The codebase is production-quality but has areas where hygiene, test coverage, and modularity can be improved. This plan proposes 10 improvements ordered by impact and risk.

---

## Improvement 1: API Endpoint Integration Tests
**Priority:** 🔴 High | **Effort:** Small | **Risk:** None

### Problem
Only 2 test files exist (`test_state_machine.py`, `test_m1_end_to_end.py`). No tests exercise the HTTP API endpoints directly (FastAPI TestClient). API regressions can go undetected.

### Proposed Change
Add `tests/test_api.py` with FastAPI TestClient covering:
- `POST /debates` — create, validation, prompt injection rejection
- `GET /debates` — list with filters
- `GET /debates/{id}` — fetch with participants
- `POST /debates/{id}/start` — host-only guard
- `POST /debates/{id}/turns` — submit, char limit, wrong speaker
- `POST /debates/{id}/scores` — duplicate rejection (409)
- `POST /debates/{id}/finalize` — incomplete scores rejection
- `POST /api/agents/join` — zero-friction join flow
- `GET /api/tasks/next` — task polling
- `GET /health`, `GET /version`

### Files Touched
- NEW: `tests/test_api.py`

---

## Improvement 2: Split src/api.py into Modular Routers
**Priority:** 🔴 High | **Effort:** Medium | **Risk:** Low

### Problem
`src/api.py` is ~2,600 lines. A monolithic API file is hard to navigate, review, and test in isolation. The project already has `docs/API_MODULARIZATION_PLAN.md` acknowledging this.

### Proposed Change
Split into 4-5 router modules under `src/routers/`:
- `src/routers/debates.py` — debate CRUD, start, cancel, delete, export
- `src/routers/turns.py` — turn submission, listing
- `src/routers/scores.py` — score submission, listing, finalize
- `src/routers/agents.py` — agent join, task polling, heartbeat, reconnect
- `src/routers/ui.py` — HTML template routes, leaderboard

Shared utilities (`_ensure_tasks_for_debate`, `broadcast_safe`, etc.) move to `src/core.py`.

### Files Touched
- NEW: `src/routers/__init__.py`, `src/routers/debates.py`, `src/routers/turns.py`, `src/routers/scores.py`, `src/routers/agents.py`, `src/routers/ui.py`, `src/core.py`
- MODIFIED: `src/api.py` → becomes thin app factory with router includes

---

## Improvement 3: Remove Dead Code and Duplicate File Trees
**Priority:** 🟡 Medium | **Effort:** Small | **Risk:** Low

### Problem
The repo has duplicate/legacy file paths that the README explicitly warns about:
- `templates/` duplicates `static/templates/`
- `elo/` duplicates `src/elo/`
- `federation/` duplicates `src/federation/`
- `tournaments/` duplicates `src/tournaments/`
- `css/`, `js/` duplicates `static/css/`, `static/js/`
- `src/federation.py.old` — dead code file
- `src/api.py` imports `src.schemas` which doesn't exist (it's `src/schemas.py`)

Non-duplicate but dead: `scripts/migrations/` SQL files that appear unused.

### Proposed Change
- Remove top-level `templates/`, `elo/`, `federation/`, `tournaments/`, `css/`, `js/` duplicate directories
- Remove `src/federation.py.old`
- Verify nothing breaks (all references point to canonical `src/*` and `static/*`)
- Add `.gitignore` entries for `.pyc`, `.pytest_cache` coverage gaps

### Files Touched
- DELETED: 6 duplicate directories, `src/federation.py.old`

---

## Improvement 4: GitHub Actions CI Pipeline
**Priority:** 🔴 High | **Effort:** Small | **Risk:** None

### Problem
No CI/CD pipeline. Tests only run manually. No automated linting, type checking, or deploy verification.

### Proposed Change
Add `.github/workflows/ci.yml`:
- Matrix: Python 3.11, 3.12
- Steps: install deps, run pytest, check syntax with `py_compile`
- Optional: mypy type check (informational only, not blocking)
- Live health check against Railway deploy (informational)

### Files Touched
- NEW: `.github/workflows/ci.yml`

---

## Improvement 5: Docker Compose for Local Development
**Priority:** 🟡 Medium | **Effort:** Small | **Risk:** None

### Problem
New contributors/devs must manually set up Python venv, install deps, and start the server. No containerized development option.

### Proposed Change
Add `Dockerfile` and `docker-compose.yml`:
- Single `docker compose up` starts the app on port 8000
- Mounts source as volume for live reload
- Includes optional Postgres service for production-parity testing

### Files Touched
- NEW: `Dockerfile`, `docker-compose.yml`, `.dockerignore`

---

## Improvement 6: Fix datetime.utcnow() Deprecation
**Priority:** 🟡 Medium | **Effort:** Trivial | **Risk:** None

### Problem
The codebase uses `datetime.utcnow()` extensively. Python 3.12+ deprecates this in favor of `datetime.now(datetime.UTC)` (or `datetime.now(tz=timezone.utc)`). There's also a mix of `_now_utc()` helper and raw `datetime.utcnow()` calls — inconsistent.

### Proposed Change
- Standardize on `datetime.now(datetime.UTC)` everywhere
- Ensure the `_now_utc()` helper in `src/api.py` is used consistently instead of raw `datetime.utcnow()` calls
- Fix the import order: `from datetime import datetime, UTC` for clarity

### Files Touched
- `src/api.py`, `src/state_machine.py`, `src/models.py`, `src/judging.py`, `src/export.py`, `src/invite_tokens.py`

---

## Improvement 7: Host Auth — Proper Token-Based Authorization
**Priority:** 🔴 High | **Effort:** Medium | **Risk:** Medium

### Problem
`start_debate` and `finalize_debate` endpoints accept `host_id` as a plain query/body parameter with no actual authentication. Any caller can supply any `host_id`. The existing fix (BLOCKER #1) checks `debate.created_by == host_id` but `host_id` is still user-supplied and trivially forgeable. This is not real authorization.

### Proposed Change
- Add a host token system: when a debate is created, generate a `host_token` (opaque, stored hashed)
- Return the raw token once at creation time (same pattern as invite tokens)
- `start_debate` and `finalize_debate` require `Authorization: Bearer <host_token>` header
- Validate host token against hashed value + debate ownership
- Add rate limiting to host endpoints

### Files Touched
- `src/models.py` — add `host_token_hash` to Debate model
- `src/api.py` — modify create/start/finalize endpoints
- `src/schemas.py` — update response schemas
- `tests/test_api.py` — test auth guards

---

## Improvement 8: Add Type Hints and Docstrings to Core Modules
**Priority:** 🟡 Medium | **Effort:** Medium | **Risk:** None

### Problem
Several public functions lack docstrings. Type hints are inconsistent — some use `Dict[str, Any]`, some use raw `dict`. The `ConnectionManager` class and timeout handler could benefit from protocol/interface documentation.

### Proposed Change
- Add docstrings to all public functions in `src/state_machine.py`, `src/judging.py`, `src/invite_tokens.py`
- Add type hints to `ConnectionManager`, `TurnTimeoutHandler`, and key helper functions
- Add module-level docstrings explaining responsibility boundaries

### Files Touched
- `src/state_machine.py`, `src/judging.py`, `src/invite_tokens.py`, `src/api.py` (type hints only)

---

## Improvement 9: Proper Alembic Migration Integration
**Priority:** 🟢 Low | **Effort:** Medium | **Risk:** Medium

### Problem
`requirements.txt` includes `alembic==1.13.1` but there's no `alembic.ini`, no `migrations/` directory in the canonical path, and no evidence Alembic is actually configured. SQL migration scripts in `scripts/migrations/` are raw SQL with no version tracking.

### Proposed Change
- Initialize Alembic with `alembic init migrations`
- Convert existing raw SQL migrations to Alembic revisions
- Add `alembic upgrade head` to CI pipeline and app startup

### Files Touched
- NEW: `alembic.ini`, `migrations/env.py`, `migrations/versions/`
- MODIFIED: `main.py` (auto-upgrade on startup)

---

## Improvement 10: Logger Configuration
**Priority:** 🟢 Low | **Effort:** Trivial | **Risk:** None

### Problem
Logging is ad-hoc — `print()` statements mixed with `logging.getLogger(__name__)` calls. No structured logging configuration. Error details in timeout sweeper use bare `print()` which won't appear in production logs.

### Proposed Change
- Add `logging.basicConfig()` to `main.py` startup with proper format and level
- Replace `print()` calls in timeout handler with proper logger calls
- Add request ID middleware for traceability

### Files Touched
- `main.py`, `src/api.py`, `src/state_machine.py`

---

## Implementation Order

| Order | Improvement | Can Proceed Without Approval? |
|-------|------------|-------------------------------|
| 1 | #3 Remove dead code | ✅ Yes — pure cleanup |
| 2 | #6 Fix datetime deprecation | ✅ Yes — pure fix |
| 3 | #10 Logger config | ✅ Yes — no behavior change |
| 4 | #4 GitHub Actions CI | ✅ Yes — additive |
| 5 | #5 Docker Compose | ✅ Yes — additive |
| 6 | #1 API integration tests | ✅ Yes — additive |
| 7 | #8 Type hints/docstrings | ✅ Yes — no behavior change |
| 8 | #2 Split API into routers | ⚠️ Needs approval — structural change |
| 9 | #7 Host auth | ⚠️ Needs approval — security change |
| 10 | #9 Alembic migrations | ⚠️ Needs approval — DB migration |
