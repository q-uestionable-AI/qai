# Design: Phase 4a — Port Audit Module

**Date:** 2026-03-14
**Status:** Approved
**Branch:** `feature/audit-module`

---

## Summary

Port the CounterAgent audit module into the qai platform. Establishes the pattern all other module ports will follow. Implements the framework-agnostic mapping concept during the port (replacing hardcoded OWASP IDs with stable internal categories).

---

## Key Decisions

1. **MCP utilities location:** `q_ai.mcp` — new top-level package alongside `q_ai.core`, `q_ai.audit`, etc.
2. **Finding model bridging:** Mapper function — scanners produce `ScanFinding`, mapper converts to core `Finding` for DB persistence.
3. **Schema migration:** Bump core schema to V2 — `audit_scans` table added in central `schema.py`.
4. **Framework-agnostic mapping:** Scanners use `category` (stable internal taxonomy) instead of `owasp_id`. FrameworkResolver populates `framework_ids` in the orchestrator.

---

## Package Structure

```
src/q_ai/
├── mcp/                          # MCP protocol utilities (shared by audit/proxy/inject/chain)
│   ├── __init__.py               # Re-exports: MCPConnection, enumerate_server, ScanFinding, etc.
│   ├── connection.py             # MCPConnection async context manager (3 transports)
│   ├── discovery.py              # enumerate_server() → ScanContext
│   ├── models.py                 # ScanFinding, ScanContext, Severity, Transport, Direction
│   └── payloads/
│       ├── __init__.py
│       └── injection.py          # InjectionPayload + payload generators
│
├── audit/                        # Audit module
│   ├── __init__.py
│   ├── cli.py                    # Typer app: scan, list-checks, enumerate, report
│   ├── orchestrator.py           # run_scan() → ScanResult
│   ├── mapper.py                 # ScanFinding → core Finding + DB persistence
│   ├── scanner/
│   │   ├── __init__.py
│   │   ├── base.py               # BaseScanner ABC
│   │   ├── registry.py           # Scanner registry
│   │   ├── injection.py          # command_injection (was MCP05)
│   │   ├── auth.py               # auth (was MCP07)
│   │   ├── token_exposure.py     # token_exposure (was MCP01)
│   │   ├── permissions.py        # permissions (was MCP02)
│   │   ├── tool_poisoning.py     # tool_poisoning (was MCP03)
│   │   ├── prompt_injection.py   # prompt_injection (was MCP06)
│   │   ├── audit_telemetry.py    # audit_telemetry (was MCP08)
│   │   ├── context_sharing.py    # context_sharing (was MCP10)
│   │   ├── supply_chain.py       # supply_chain (was MCP04)
│   │   └── shadow_servers.py     # shadow_servers (was MCP09)
│   └── reporting/
│       ├── __init__.py
│       ├── json_report.py
│       ├── sarif_report.py
│       ├── html_report.py
│       ├── severity.py
│       └── prompt.py
```

---

## Model Bridging & Data Flow

### Scan data flow

```
CLI (qai audit scan)
  → orchestrator.run_scan()
    → MCPConnection (connect to target server)
    → enumerate_server() → ScanContext
    → for each scanner: scanner.scan(context) → list[ScanFinding]
    → FrameworkResolver.resolve(finding.category) → populate framework_ids
    → ScanResult (all findings + metadata)
  → mapper.persist_scan(scan_result, db_path)
    → create_target() (type="mcp_server")
    → create_run() (module="audit")
    → for each ScanFinding → create_finding() (mapped to core Finding)
    → insert audit_scans row (module-specific metadata)
    → update_run_status(COMPLETED)
  → reporter generates JSON/SARIF/HTML from ScanResult (unchanged)
```

### ScanFinding → core Finding field mapping

| ScanFinding field | Core Finding field | Notes |
|---|---|---|
| `category` | `category` | Direct pass-through |
| `rule_id` | `source_ref` | e.g., `QAI-INJ-CWE88-flag_injection` |
| `severity` | `severity` | MCP Severity enum → core Severity IntEnum |
| `framework_ids` | `framework_ids` | Populated by orchestrator via FrameworkResolver |
| `title` | `title` | Direct pass-through |
| `description` + `remediation` + `evidence` + `tool_name` | `description` | Combined into rich text |
| (hardcoded) | `module` | `"audit"` |

Reports continue to consume `ScanResult` directly — they need `remediation`, `evidence`, and `metadata` fields that the core DB doesn't store.

### Mapper function signature

```python
def persist_scan(scan_result: ScanResult, db_path: Path) -> str:
    """Persist scan results to the shared DB. Returns run_id."""
```

---

## Framework-Agnostic Scanner Refactor

### BaseScanner attribute change

```python
# Before (counteragent):
class InjectionScanner(BaseScanner):
    name = "injection"
    owasp_id = "MCP05"

# After (qai):
class InjectionScanner(BaseScanner):
    name = "injection"
    category = "command_injection"
```

### Category assignments

| Scanner | Old `owasp_id` | New `category` | Abbreviation |
|---|---|---|---|
| injection | MCP05 | command_injection | INJ |
| auth | MCP07 | auth | AUTH |
| token_exposure | MCP01 | token_exposure | TOK |
| permissions | MCP02 | permissions | PERM |
| tool_poisoning | MCP03 | tool_poisoning | TPOIS |
| prompt_injection | MCP06 | prompt_injection | PINJ |
| audit_telemetry | MCP08 | audit_telemetry | AUDIT |
| supply_chain | MCP04 | supply_chain | SCHAIN |
| shadow_servers | MCP09 | shadow_servers | SHADOW |
| context_sharing | MCP10 | context_sharing | CTX |

### Rule ID format

Old: `MCP05-CWE88-flag_injection`
New: `QAI-INJ-CWE88-flag_injection`

Prefix `QAI`, then category abbreviation, then weakness type, then technique.

### ScanFinding model changes

- `owasp_id` field → removed
- `category` field → added (set by scanner, stable internal ID)
- `framework_ids` field → added (empty dict from scanner, populated by orchestrator)

### Orchestrator integration

After collecting all findings from scanners, the orchestrator calls:
```python
resolver = FrameworkResolver()
for finding in findings:
    finding.framework_ids = resolver.resolve(finding.category)
```

### list-checks CLI

Default: shows category column. `--framework` flag shows specific framework IDs via FrameworkResolver.

### Report backward compatibility

JSON reports include `owasp_id` field (populated from `framework_ids["owasp_mcp_top10"]`) alongside `category` + `framework_ids` for backward compatibility.

---

## Schema Migration

V2 migration adds `audit_scans` table:

```sql
CREATE TABLE IF NOT EXISTS audit_scans (
    id                    TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    run_id                TEXT NOT NULL REFERENCES runs(id),
    transport             TEXT NOT NULL,
    server_name           TEXT,
    server_version        TEXT,
    scanners_run          TEXT,       -- JSON array
    finding_count         INTEGER DEFAULT 0,
    scan_duration_seconds REAL,
    created_at            TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_scans_run_id ON audit_scans(run_id);
```

`migrate()` gains a V2 step: if `PRAGMA user_version < 2`, execute V2 DDL and set `user_version = 2`.

New DB helper:
```python
def create_audit_scan(db_path, *, run_id, transport, server_name, server_version,
                      scanners_run, finding_count, scan_duration_seconds) -> str:
```

---

## CLI Wiring

Typer app in `audit/cli.py`, mounted on root CLI as `qai audit`.

### Commands

1. **`qai audit scan`** — Main scan command
   - `--transport` (stdio/sse/streamable-http, required)
   - `--command` (for stdio transport)
   - `--url` (for SSE/streamable-http)
   - `--checks` (comma-separated scanner names, default: all)
   - `--format` (json/sarif/html, default: json)
   - `--output` (output path, default: auto-generated)
   - `--verbose` (show detailed progress)
   - Runs scan, persists to DB via mapper, generates report file

2. **`qai audit list-checks`** — List available scanners
   - Rich table: Name, Category, Description, Framework IDs
   - `--framework` flag to show specific framework IDs

3. **`qai audit enumerate`** — Enumerate without scanning
   - Same transport args as scan
   - Displays tools, resources, prompts in tables

4. **`qai audit report`** — Re-generate report from saved JSON
   - `--input` (path to JSON results)
   - `--format` (json/sarif/html)
   - `--output` (output path)

---

## Web UI Integration

### Operations view — Audit tab

New functional tab in `operations.html`:
- Scan launch form: transport select, command/url input, check selection, go button
- Live findings table updated via HTMX polling or WebSocket

### New API routes

- `POST /api/audit/scan` — Start scan in background, return run_id
- `GET /api/audit/scan/{run_id}/status` — Scan progress (HTMX polling)
- `GET /api/audit/findings/{run_id}` — Findings partial for a specific scan run

### New templates

- `templates/partials/audit_tab.html` — Audit operations panel
- `templates/partials/audit_findings.html` — Findings partial

### Research view

No changes needed — audit data flows through shared DB tables and appears in existing runs/findings views automatically.

---

## Dependencies

Add to `pyproject.toml`:
- `mcp>=1.26,<2` — MCP SDK
- `pydantic>=2.0` — Used by MCP SDK types

Only what audit actually needs. `fastmcp` and `anthropic` deferred to later module ports.

---

## Tests

Port all tests from `counteragent/tests/audit/` and relevant core tests. Update:
- All `from counteragent.` → `from q_ai.`
- All `owasp_id` assertions → `category` assertions
- All `MCP05-...` rule_id assertions → `QAI-INJ-...` format

New tests for:
- `audit/mapper.py` — ScanFinding → core Finding conversion, DB persistence
- `audit_scans` table CRUD
- V2 schema migration

Existing qai tests must continue to pass unchanged.

---

## Acceptance Criteria

1. `qai audit scan --transport stdio --command "python my_server.py"` works
2. `qai audit list-checks` displays all 10 scanner modules with categories
3. `qai audit enumerate` discovers server capabilities
4. Scan findings written to `~/.qai/qai.db` (visible via `qai findings list`)
5. Run record created in DB for each scan
6. JSON/HTML/SARIF report generation works
7. `audit_scans` table exists with per-scan metadata
8. MCP utilities in `q_ai.mcp` importable by future modules
9. No remaining `counteragent` import references in qai source
10. All scanners use `category` instead of `owasp_id`
11. Rule IDs use `QAI-{ABBREV}-...` format
12. `framework_ids` populated via FrameworkResolver
13. All ported tests pass
14. All existing qai tests pass
15. Pre-commit and mypy pass
16. Audit tab visible in web UI operations view
