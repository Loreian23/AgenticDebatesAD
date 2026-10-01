-- =============================================================================
-- Migration: 001_add_agent_queue
-- Description: Adds AgentSession and AgentTask tables for background worker queue
-- Applied: 2026-04-10
-- =============================================================================
-- This migration is idempotent — safe to run multiple times.
-- Apply with: sqlite3 debates.db < scripts/migrations/001_add_agent_queue.sql
-- =============================================================================

-- AgentSession: registered API agent sessions per debate
CREATE TABLE IF NOT EXISTS agent_sessions (
    id TEXT PRIMARY KEY,
    debate_id TEXT NOT NULL,
    participant_id TEXT,
    agent_name TEXT NOT NULL,
    model_name TEXT,
    preferred_role TEXT NOT NULL DEFAULT 'auto',
    assigned_side TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'auto',
    token_hash TEXT NOT NULL UNIQUE,
    token_preview TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    is_active INTEGER DEFAULT 1,
    metadata_json TEXT DEFAULT '{}',
    FOREIGN KEY (debate_id) REFERENCES debates(id) ON DELETE CASCADE,
    FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_session_debate ON agent_sessions(debate_id);
CREATE INDEX IF NOT EXISTS idx_agent_session_active ON agent_sessions(is_active, expires_at);

-- AgentTask: background task queue items for agent workers
CREATE TABLE IF NOT EXISTS agent_tasks (
    id TEXT PRIMARY KEY,
    debate_id TEXT NOT NULL,
    participant_id TEXT,
    assigned_side TEXT NOT NULL,
    phase TEXT NOT NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT DEFAULT '{}',
    completion_json TEXT DEFAULT '{}',
    dedupe_key TEXT NOT NULL UNIQUE,
    completion_idempotency_key TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    available_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    lease_expires_at TIMESTAMP,
    leased_by_session_id TEXT,
    completed_at TIMESTAMP,
    FOREIGN KEY (debate_id) REFERENCES debates(id) ON DELETE CASCADE,
    FOREIGN KEY (participant_id) REFERENCES participants(id) ON DELETE SET NULL,
    FOREIGN KEY (leased_by_session_id) REFERENCES agent_sessions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_task_debate_status ON agent_tasks(debate_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_task_participant ON agent_tasks(participant_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_task_lease ON agent_tasks(status, lease_expires_at);

-- Done
SELECT 'Migration 001_add_agent_queue applied successfully' AS status;
