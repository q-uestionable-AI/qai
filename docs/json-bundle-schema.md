# JSON Bundle Schema — `run-bundle-v1`

Schema reference for the JSON export bundle produced by `qai audit export` and the web UI Export JSON button.

## Top-Level Structure

```json
{
  "schema_version": "run-bundle-v1",
  "run": { ... },
  "child_runs": [ ... ],
  "findings": [ ... ],
  "evidence": [ ... ],
  "target": { ... },
  "audit_scans": [ ... ],
  "inject_results": [ ... ],
  "proxy_sessions": [ ... ]
}
```

## `run` — Parent Run

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID |
| `module` | string | Always `"workflow"` for parent runs |
| `name` | string \| null | Workflow name (e.g., `"assess_mcp_server"`) |
| `target_id` | string \| null | Target UUID |
| `config` | object \| null | Workflow configuration |
| `status` | integer | RunStatus enum (0=PENDING, 1=RUNNING, 2=COMPLETED, 3=FAILED, 4=CANCELLED, 5=WAITING_FOR_USER, 6=PARTIAL) |
| `started_at` | string \| null | ISO 8601 timestamp |
| `finished_at` | string \| null | ISO 8601 timestamp |
| `parent_run_id` | string \| null | Parent run UUID (null for top-level) |

## `child_runs` — Module Runs

Array of run objects (same schema as `run`). One per module executed (audit, inject, proxy, etc.).

## `findings` — Security Findings

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID |
| `run_id` | string | Run that produced this finding |
| `module` | string | Module name (`"audit"`, `"inject"`, etc.) |
| `category` | string | Finding category (e.g., `"command_injection"`) |
| `severity` | integer | 0=INFO, 1=LOW, 2=MEDIUM, 3=HIGH, 4=CRITICAL |
| `title` | string | Short human-readable title |
| `description` | string \| null | Detailed description |
| `framework_ids` | object \| null | `{"owasp_mcp_top10": "MCP05", "cwe": ["CWE-78"]}` |
| `mitigation` | object \| null | Structured mitigation guidance (see below) |
| `source_ref` | string \| null | Source reference (tool name, file, line) |
| `created_at` | string | ISO 8601 timestamp |

### `mitigation` — Mitigation Guidance

Null for findings generated before mitigation was added. When present:

| Field | Type | Description |
|-------|------|-------------|
| `sections` | array | Ordered list of guidance sections |
| `caveats` | array | Top-level caveats (strings) |
| `schema_version` | integer | Currently `1` |
| `disclaimer` | string | Standard non-exhaustiveness disclaimer |

#### `sections[n]` — Guidance Section

| Field | Type | Description |
|-------|------|-------------|
| `kind` | string | `"actions"` or `"factors"` |
| `source_type` | string | `"taxonomy"`, `"rule"`, or `"context"` |
| `source_ids` | array | Framework names, predicate IDs, or category names |
| `items` | array | Guidance text strings |

## `evidence` — Evidence References

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID |
| `finding_id` | string | Associated finding UUID |
| `run_id` | string | Associated run UUID |
| `type` | string | Evidence type |
| `mime_type` | string \| null | MIME type |
| `hash` | string \| null | Content hash |
| `storage` | string | `"inline"` or `"file"` |
| `created_at` | string | ISO 8601 timestamp |

Note: Evidence content and file paths are not included in the bundle metadata. Inline evidence content is available via the findings detail view.

## `target` — Scan Target

Null if no target was associated with the run.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | UUID |
| `type` | string | Target type |
| `name` | string | Display name |
| `uri` | string \| null | Target URI |
| `metadata` | object \| null | Additional target metadata |
| `created_at` | string | ISO 8601 timestamp |

## Compatibility

- `schema_version` is `"run-bundle-v1"` — changes to the structure will increment this version
- `mitigation.schema_version` is `1` — consumers should handle unknown versions gracefully
- Null fields are always present as keys (not omitted)
- Module-specific arrays (`audit_scans`, `inject_results`, etc.) may be empty arrays
