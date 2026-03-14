"""Database schema definitions and migration for q-ai."""
from __future__ import annotations

import sqlite3

CURRENT_VERSION = 1

V1_TABLES = """
CREATE TABLE IF NOT EXISTS targets (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    name TEXT NOT NULL,
    uri TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    parent_run_id TEXT REFERENCES runs(id),
    module TEXT NOT NULL,
    name TEXT,
    target_id TEXT REFERENCES targets(id),
    config TEXT,
    status INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    module TEXT NOT NULL,
    category TEXT NOT NULL,
    severity INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    framework_ids TEXT,
    source_ref TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    finding_id TEXT REFERENCES findings(id),
    run_id TEXT REFERENCES runs(id),
    type TEXT NOT NULL,
    mime_type TEXT,
    hash TEXT,
    storage TEXT NOT NULL DEFAULT 'inline',
    content TEXT,
    path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL
);
"""

V1_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_runs_parent_run_id ON runs(parent_run_id);
CREATE INDEX IF NOT EXISTS idx_runs_module ON runs(module);
CREATE INDEX IF NOT EXISTS idx_runs_target_id ON runs(target_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_findings_run_id ON findings(run_id);
CREATE INDEX IF NOT EXISTS idx_findings_module ON findings(module);
CREATE INDEX IF NOT EXISTS idx_findings_category ON findings(category);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_evidence_finding_id ON evidence(finding_id);
CREATE INDEX IF NOT EXISTS idx_evidence_run_id ON evidence(run_id);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Run schema migrations up to CURRENT_VERSION.

    Checks PRAGMA user_version and runs any pending migrations sequentially.
    Version 1 creates all shared tables and indexes.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(V1_TABLES)
        conn.executescript(V1_INDEXES)
        conn.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
