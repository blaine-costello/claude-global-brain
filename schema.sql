-- Local Claude brain — machine-wide cross-session memory.
-- Append-only event log (adapted from a production AI-agent memory system) + FTS5 + decay/usage scoring.
-- Apply with: sqlite3 brain.db < schema.sql  (brain.py also applies this idempotently)

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

-- Core event log. Every row's payload_json MUST include a "summary" field
-- (that's what readers/recall see first).
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,                 -- ISO 8601 UTC: when it happened
    source          TEXT    NOT NULL,                 -- claude-code | user | session | git | ...
    type            TEXT    NOT NULL,                 -- decision | gotcha | preference | fix | bug | session.summary | consolidated | ...
    actor           TEXT,                             -- e.g. user, project slug, session id
    project         TEXT,                             -- project slug/path (scoping); NULL = global
    session_id      TEXT,                             -- originating Claude session
    payload_json    TEXT    NOT NULL,                 -- JSON; MUST include "summary"
    confidence      REAL    NOT NULL DEFAULT 0.5,     -- caller's stated confidence (0..1)
    quality_score   REAL    NOT NULL DEFAULT 0.0,     -- usage-feedback score (-3..+3), daemon-updated
    superseded_by   INTEGER REFERENCES events(id),    -- newer event that replaced this one
    parent_id       INTEGER REFERENCES events(id),    -- causal chaining
    consolidated_at TEXT,                             -- set once rolled into a wiki/consolidated doc
    ingested_at     TEXT    NOT NULL                  -- ISO 8601 UTC: when the row hit the DB
);

CREATE INDEX IF NOT EXISTS idx_events_ts         ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_src_type   ON events(source, type);
CREATE INDEX IF NOT EXISTS idx_events_actor      ON events(actor);
CREATE INDEX IF NOT EXISTS idx_events_project    ON events(project);
CREATE INDEX IF NOT EXISTS idx_events_superseded ON events(superseded_by);
CREATE INDEX IF NOT EXISTS idx_events_ingested   ON events(ingested_at);

-- Full-text index over the human-readable fields (keyword + fuzzy recall).
-- Regular FTS5 table (stores its own copy); kept in sync manually by brain.py
-- using rowid = events.id. Falls back to LIKE search if FTS5 is unavailable.
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    summary,
    type,
    project,
    tokenize = 'porter unicode61'
);

-- Per-type (optionally per-scope) decay half-life overrides, in days.
CREATE TABLE IF NOT EXISTS decay_halflife_override (
    event_type    TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT '',
    halflife_days REAL NOT NULL CHECK (halflife_days > 0),
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (event_type, scope)
);

-- Usage feedback: which events were injected into which session, so the daemon
-- can raise quality_score for memories that preceded good outcomes.
CREATE TABLE IF NOT EXISTS injection_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL,
    session_id  TEXT,
    project     TEXT,
    injected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_injection_event ON injection_log(event_id);

-- Convenience view: most-recent live event per (project, type).
CREATE VIEW IF NOT EXISTS latest_by_project_type AS
SELECT project, type, MAX(ts) AS last_ts, COUNT(*) AS n
FROM events
WHERE superseded_by IS NULL
GROUP BY project, type;
