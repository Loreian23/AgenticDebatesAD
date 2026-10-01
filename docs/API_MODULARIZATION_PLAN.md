# API Modularization Plan (`src/api.py`)

## Goal
Reduce `src/api.py` (~4k LOC) into domain routers with no behavior change.

## Target structure

- `src/routes/debates.py`
- `src/routes/judging.py`
- `src/routes/federation.py`
- `src/routes/elo.py`
- `src/routes/system.py` (health/version/llms)
- `src/routes/websocket.py`
- `src/routes/html.py`

## Migration rules

1. **No route-path changes** in refactor PRs.
2. Move one domain at a time; keep PRs under ~400 LOC changed.
3. Preserve existing dependency wiring and middleware order.
4. After each move, run:
   - `PYTHONPATH=. pytest -q tests`
   - smoke checks for `/health`, `/version`, `/docs`, and one debate flow.
5. Add temporary import shims only when needed; remove them by final phase.

## Suggested phases

### Phase 1: system routes
Move `/health`, `/version`, `/llms.txt`, `/llms-full.txt` first (lowest coupling).

### Phase 2: federation + elo
Promote existing `src/federation/api.py` and `src/elo/api.py` as included routers, then remove duplicate local handlers from `src/api.py` where present.

### Phase 3: debate CRUD + state transitions
Move debate creation/list/start/turn/finalize endpoints.

### Phase 4: websocket + HTML routes
Move websocket route and Jinja HTML handlers.

### Phase 5: cleanup
Consolidate shared dependencies and remove dead imports.

## Definition of done

- `src/api.py` reduced to app factory + middleware + `include_router(...)`
- all existing tests pass
- no route contract regressions
