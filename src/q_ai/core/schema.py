"""Database schema definitions and migration for q-ai."""

from __future__ import annotations

import sqlite3

CURRENT_VERSION = 5

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


V2_TABLES = """
CREATE TABLE IF NOT EXISTS audit_scans (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    run_id TEXT NOT NULL REFERENCES runs(id),
    transport TEXT NOT NULL,
    server_name TEXT,
    server_version TEXT,
    scanners_run TEXT,
    finding_count INTEGER DEFAULT 0,
    scan_duration_seconds REAL,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

V2_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_audit_scans_run_id ON audit_scans(run_id);
"""

V3_TABLES = """
CREATE TABLE IF NOT EXISTS inject_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    payload_name TEXT NOT NULL,
    technique TEXT NOT NULL,
    outcome TEXT NOT NULL,
    target_agent TEXT NOT NULL,
    evidence TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

V3_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_inject_results_run_id ON inject_results(run_id);
"""

V4_TABLES = """
CREATE TABLE IF NOT EXISTS proxy_sessions (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    run_id TEXT NOT NULL REFERENCES runs(id),
    transport TEXT NOT NULL,
    server_name TEXT,
    message_count INTEGER DEFAULT 0,
    duration_seconds REAL,
    session_file TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

V4_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_proxy_sessions_run_id ON proxy_sessions(run_id);
"""

V5_TABLES = """
CREATE TABLE IF NOT EXISTS chain_executions (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    run_id TEXT NOT NULL REFERENCES runs(id),
    chain_id TEXT NOT NULL,
    chain_name TEXT,
    dry_run INTEGER NOT NULL DEFAULT 1,
    template_path TEXT,
    target_config TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    trust_boundaries TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS chain_step_outputs (
    id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    execution_id TEXT NOT NULL REFERENCES chain_executions(id),
    step_id TEXT NOT NULL,
    module TEXT NOT NULL,
    technique TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0,
    status TEXT,
    artifacts TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""

V5_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_chain_executions_run_id ON chain_executions(run_id);
CREATE INDEX IF NOT EXISTS idx_chain_step_outputs_execution_id ON chain_step_outputs(execution_id);
"""


def migrate(conn: sqlite3.Connection) -> None:
    """Run schema migrations up to CURRENT_VERSION.

    Checks PRAGMA user_version and runs any pending migrations sequentially.
    Version 1 creates all shared tables and indexes.
    Version 2 adds the audit_scans table.
    Version 3 adds the inject_results table.
    Version 4 adds the proxy_sessions table.
    Version 5 adds chain_executions and chain_step_outputs tables.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        conn.executescript(V1_TABLES)
        conn.executescript(V1_INDEXES)
        conn.execute("PRAGMA user_version = 1")
        version = 1
    if version < 2:
        conn.executescript(V2_TABLES)
        conn.executescript(V2_INDEXES)
        conn.execute("PRAGMA user_version = 2")
    if version < 3:
        conn.executescript(V3_TABLES)
        conn.executescript(V3_INDEXES)
        conn.execute("PRAGMA user_version = 3")
    if version < 4:
        conn.executescript(V4_TABLES)
        conn.executescript(V4_INDEXES)
        conn.execute("PRAGMA user_version = 4")
    if version < 5:
        conn.executescript(V5_TABLES)
        conn.executescript(V5_INDEXES)
        conn.execute("PRAGMA user_version = 5")
