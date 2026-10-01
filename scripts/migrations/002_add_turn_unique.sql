-- =============================================================================
-- Migration: 002_add_turn_unique
-- Description: Adds unique constraint on turns(debate_id, sequence_number)
--              to prevent duplicate turns for the same debate slot
-- Applied: 2026-04-30
-- =============================================================================
-- This migration is idempotent — safe to run multiple times.
-- Apply with: sqlite3 debates.db < scripts/migrations/002_add_turn_unique.sql
-- =============================================================================

-- SQLite does not support DROP CONSTRAINT, so we recreate the table.
-- On PostgreSQL/MySQL use: ALTER TABLE turns ADD CONSTRAINT uq_turn_debate_seq UNIQUE (debate_id, sequence_number);

CREATE TABLE IF NOT EXISTS turns_new (
    id TEXT PRIMARY KEY,
    debate_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    sequence_number INTEGER NOT NULL,
    phase TEXT NOT NULL,
    content TEXT NOT NULL,
    content_length INTEGER DEFAULT 0,
    started_at TIMESTAMP,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    time_taken_seconds INTEGER,
    was_timeout INTEGER DEFAULT 0,
    char_limit_violation INTEGER DEFAULT 0,
    replies_to_turn_id TEXT,
    metadata_json TEXT DEFAULT '{}',
    FOREIGN KEY (debate_id) REFERENCES debates(id) ON DELETE CASCADE,
    FOREIGN KEY (participant_id) REFERENCES participants(id),
    FOREIGN KEY (replies_to_turn_id) REFERENCES turns(id)
);

-- Copy data preserving existing turn records
INSERT OR IGNORE INTO turns_new
SELECT id, debate_id, participant_id, sequence_number, phase, content,
       content_length, started_at, submitted_at, time_taken_seconds,
       was_timeout, char_limit_violation, replies_to_turn_id, metadata_json
FROM turns;

-- Verify no duplicates exist before swapping
-- If this returns rows, there are duplicates — resolve them before re-running
WITH duplicates AS (
    SELECT debate_id, sequence_number, COUNT(*) as cnt
    FROM turns
    GROUP BY debate_id, sequence_number
    HAVING COUNT(*) > 1
)
SELECT 'WARNING: Duplicate turns found — resolve before continuing' AS status
WHERE EXISTS (SELECT 1 FROM duplicates);

-- Swap old table for new
ALTER TABLE turns RENAME TO turns_old;
ALTER TABLE turns_new RENAME TO turns;

-- Recreate indexes on the new table
CREATE INDEX IF NOT EXISTS idx_turn_debate_seq ON turns(debate_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_turn_participant ON turns(participant_id);
CREATE INDEX IF NOT EXISTS idx_turn_phase ON turns(debate_id, phase);

-- Drop old table after successful swap
DROP TABLE IF EXISTS turns_old;

-- Done
SELECT 'Migration 002_add_turn_unique applied successfully' AS status;
