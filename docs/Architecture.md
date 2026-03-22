# {q-AI} — Architecture

## Overview

{q-AI} is a unified offensive security platform for agentic AI infrastructure. Seven research modules
are combined into a single Python package (`q-uestionable-ai`) with a shared CLI, shared SQLite
database, and a local web UI for orchestrating multi-module workflows.

The platform is built around the **runs-as-backbone** principle: every operation — a scan, a campaign,
a chain execution, a proxy session — creates a run record with parent/child lineage. The web UI,
CLI, and research management views are all consumers of the same `~/.qai/qai.db` database.

---

## Package Layout

```
src/q_ai/
├── __init__.py                     # Package version
├── __main__.py                     # python -m q_ai support
├── cli.py                          # Root Typer app — mounts all module subcommands
├── core/                           # Shared database, models, config, LLM abstraction
│   ├── bridge_token.py              # Bridge token for IPI hit WS bridge (~/.qai/bridge.token)
│   ├── cli/                        # Shared CLI commands (config, runs, findings, targets, update-frameworks)
│   ├── config.py                   # Config loader, keyring credential store, settings resolver
│   ├── data/frameworks.yaml        # Framework mapping data (OWASP MCP, OWASP Agentic, MITRE ATLAS, CWE)
│   ├── data/mitigations.yaml       # Mitigation guidance data (tier 1 actions, tier 2 rules, tier 3 factors)
│   ├── db.py                       # Connection manager, CRUD for shared tables, schema migration
│   ├── frameworks.py               # FrameworkResolver — category → framework IDs
│   ├── mitigation.py               # MitigationResolver — ScanFinding → MitigationGuidance
│   ├── llm.py                      # ProviderClient protocol, NormalizedResponse, ToolSpec, ToolCall
│   ├── llm_litellm.py              # LiteLLMClient — only file importing litellm
│   ├── models.py                   # Run, Target, Finding, Evidence, Severity, RunStatus
│   ├── schema.py                   # DDL for shared tables, PRAGMA user_version migration runner
│   └── update_frameworks.py        # Framework update checker — ATLAS YAML diff, OWASP version detection, cache
├── mcp/                            # MCP connection utilities (shared by audit, proxy, chain)
│   ├── connection.py               # MCPConnection — establish MCP client connections
│   ├── discovery.py                # enumerate_server() — capability discovery
│   └── models.py                   # Transport enum, MCP-specific models
├── orchestrator/                   # Workflow orchestration engine
│   ├── registry.py                 # WorkflowEntry, WORKFLOWS dict, list_workflows()
│   ├── runner.py                   # WorkflowRunner — parent/child runs, events, wait_for_user()
│   └── workflows/
│       └── assess.py               # assess_mcp_server() workflow executor
├── server/                         # FastAPI web UI
│   ├── app.py                      # Application factory, startup/shutdown lifecycle
│   ├── routes.py                   # All routes: launcher, operations, research, settings, WS
│   ├── websocket.py                # ConnectionManager — broadcast events to connected clients
│   ├── static/                     # HTMX, app.css, ws.js
│   └── templates/                  # Jinja2 templates (base, launcher, operations, research, settings)
├── audit/                          # MCP server security scanner
│   ├── adapter.py                  # AuditAdapter — orchestrator integration
│   ├── cli.py                      # audit subcommands (scan, list-checks, enumerate, report)
│   ├── mapper.py                   # ScanResult → core DB persistence
│   ├── orchestrator.py             # run_scan() entry point
│   └── scanner/                    # 10 scanner modules (one per OWASP MCP Top 10 category)
├── proxy/                          # MCP traffic interceptor
│   ├── adapter.py                  # ProxyAdapter — orchestrator integration (start/stop)
│   ├── cli.py                      # proxy subcommands (start, replay, export, inspect)
│   ├── adapters/                   # Transport adapters (stdio, SSE, streamable-http)
│   ├── pipeline.py                 # Bidirectional message forwarding
│   ├── intercept.py                # InterceptEngine (hold/release/drop)
│   └── session_store.py            # Ordered ProxyMessage capture
├── inject/                         # Tool poisoning and prompt injection testing
│   ├── adapter.py                  # InjectAdapter — orchestrator integration
│   ├── campaign.py                 # Campaign executor — provider-agnostic via ProviderClient
│   ├── cli.py                      # inject subcommands (serve, campaign, list-payloads, report)
│   ├── mapper.py                   # Campaign → core DB persistence
│   ├── scoring.py                  # Response outcome scorer (NormalizedResponse → InjectionOutcome)
│   └── payloads/                   # YAML payload templates + loader
├── chain/                          # Multi-step attack chain executor
│   ├── adapter.py                  # ChainAdapter — orchestrator integration
│   ├── cli.py                      # chain subcommands (run, validate, list-templates, blast-radius, detect)
│   ├── executor.py                 # Live execution engine
│   ├── loader.py                   # YAML chain definition discovery and parsing
│   ├── mapper.py                   # ChainResult → core DB persistence
│   ├── tracer.py                   # Dry-run path tracer
│   ├── validator.py                # Semantic validation (cycle detection, reachability)
│   └── templates/                  # Built-in chain YAML files
├── ipi/                            # Indirect prompt injection — payload generation and tracking
│   ├── adapter.py                  # IPIAdapter — orchestrator integration (with wait_for_user)
│   ├── cli.py                      # ipi subcommands (generate, listen, hits, campaigns)
│   ├── generate_service.py         # Shared generation entry point (CLI + adapter)
│   ├── guidance_builder.py         # IPI RunGuidance builder (inventory, trigger prompts, deployment, monitoring)
│   ├── mapper.py                   # Generate operations → core DB persistence
│   └── generators/                 # Format-specific generators (pdf, md, html, docx, ics, eml, image)
├── cxp/                            # Coding assistant context file poisoning
│   ├── adapter.py                  # CXPAdapter — orchestrator integration (with wait_for_user)
│   ├── builder.py                  # Assembles poisoned context repos from templates + rules
│   ├── catalog.py                  # Rule catalog loader (built-in + user rules)
│   ├── cli.py                      # cxp subcommands (build, validate, list-rules, list-formats)
│   ├── guidance_builder.py         # CXP RunGuidance builder (inventory, trigger prompts, deployment, interpretation)
│   └── mapper.py                   # Build operations → core DB persistence
└── rxp/                            # RAG retrieval poisoning measurement
    ├── adapter.py                  # RXPAdapter — orchestrator integration
    ├── cli.py                      # rxp subcommands (validate, list-models, list-profiles)
    ├── mapper.py                   # Validation results → core DB persistence
    ├── profiles/                   # Built-in domain profiles (corpus + poison + queries)
    ├── registry.py                 # Embedding model registry
    └── validator.py                # validate_retrieval() — ChromaDB-based retrieval measurement
```

---

## Core Layer (`core/`)

The core module is the integration surface. All modules read and write through it.

- **`db.py`** — SQLite connection manager with WAL mode, schema migration on connect, and common CRUD for shared tables (`runs`, `targets`, `findings`, `evidence`, `settings`).
- **`models.py`** — `Run`, `Target`, `Finding`, `Evidence`, `Severity` (IntEnum: INFO=0..CRITICAL=4), `RunStatus` (IntEnum: PENDING=0..PARTIAL=6).
- **`schema.py`** — DDL for all shared tables plus module-specific tables. Schema version tracked via `PRAGMA user_version`.
- **`config.py`** — OS keyring for API keys. Non-secret settings in `~/.qai/config.yaml` and DB `settings` table. Provider defaults stored as `default_provider` + `default_model_id` (migrated from legacy `default_model` on first read). Precedence: CLI flag → env var → keyring/DB setting/config file → default.
- **`llm.py`** — `ProviderClient` protocol, `NormalizedResponse`, `ToolSpec`, `ToolCall`. `provider/model` string convention. The litellm runtime string is composed at launch time from separate `provider` and `model_id` fields stored in settings.
- **`llm_litellm.py`** — The only file that imports `litellm`. If litellm needs replacing, only this file changes.
- **`frameworks.py`** — `FrameworkResolver` resolves `category` strings (e.g., `tool_poisoning`) to OWASP MCP Top 10, OWASP Agentic Top 10, MITRE ATLAS, and CWE IDs. All four frameworks are fully mapped for all 10 scanner categories. The `category` field is the canonical taxonomy; framework IDs are derived. ATLAS mappings verified against v5.4.0.
- **`mitigation.py`** — `MitigationResolver` generates structured `MitigationGuidance` for each `ScanFinding`. Three-tier guidance: Tier 1 taxonomy actions (per-category from `mitigations.yaml`), Tier 2 rule actions (metadata predicates matched against rule table), Tier 3 contextual factors. The resolver is a pure function — no DB, template, or I/O access after YAML load. Data models: `GuidanceSection` (kind, source_type, source_ids, items), `MitigationGuidance` (sections, caveats, schema_version, disclaimer). `SectionKind` and `SourceType` are `StrEnum` types. The normalization layer converts scanner metadata to canonical predicates via `PREDICATE_MAP` (static) and extraction functions (compound). Positioned in the scan pipeline after framework mapping, before persistence.
- **`guidance.py`** — `RunGuidance` and `GuidanceBlock` data models for per-run deployment playbooks. `BlockKind` StrEnum (inventory, trigger_prompts, deployment_steps, monitoring, interpretation, factors). `GuidanceBlock` holds a kind, label, items list, and flexible metadata dict. `RunGuidance` is the container: blocks list, schema_version, generated_at timestamp, and originating module. Both provide `to_dict()` / `from_dict()` with fail-soft deserialization (unknown schema_version returns a fallback block). Stored as JSON in the `guidance` column of the `runs` table. **Distinct from `MitigationGuidance`**: `MitigationGuidance` is per-finding, generated by the `MitigationResolver`, and attached to audit findings. `RunGuidance` is per-run, generated by module adapters (IPI, CXP, RXP) at run creation time, and attached to the run record. Together they form two layers of a guidance system.
- **`bridge_token.py`** — Shared secret for the IPI hit WebSocket bridge. `ensure_bridge_token()` generates a 32-char hex token at `~/.qai/bridge.token` with 0600 permissions (Unix) on first use; `read_bridge_token()` reads it. Both the IPI callback server and the main qai server use this token for internal bridge API auth.
- **`update_frameworks.py`** — `check_frameworks()` fetches the structured ATLAS.yaml from the latest GitHub release, diffs technique IDs against local mappings, and checks the OWASP MCP Top 10 page for version changes. Results cached 24h at `~/.qai/cache/framework_updates.json`. Never writes to `frameworks.yaml`.

### Provider Registry (`core/providers.py`)

Single source of truth for provider definitions. `PROVIDERS` dict maps provider keys to `ProviderConfig` dataclasses with type (CLOUD/LOCAL/CUSTOM), curated model lists, endpoint URLs, and capability flags. `fetch_models()` enumerates available models from local providers (Ollama, LM Studio) via their APIs with a 3s timeout, or returns curated lists for cloud providers. `get_configured_providers()` checks credential and base_url presence across all registered providers.

---

## Orchestrator (`orchestrator/`)

Manages multi-module workflow runs.

- **`WorkflowRunner`** — `create_child_run()`, `update_child_status()`, `emit()`, `emit_progress()`, `emit_finding()`, `wait_for_user()`, `resume()`. All DB writes and WebSocket events go through the runner.
- **`registry.py`** — `WorkflowEntry` metadata (name, description, modules, executor). `list_workflows()` serves the launcher.
- **`workflows/assess.py`** — `assess_mcp_server()`: audit → proxy (background) + inject, `best_effort` error handling, proxy cleanup in `try/finally`.

**Module adapters** — colocated with each module:

| Adapter | Interface | Error policy |
|---------|-----------|--------------|
| `audit/adapter.py` | `run()` | fail on exception |
| `proxy/adapter.py` | `start()` / `stop()` | fail on exception |
| `inject/adapter.py` | `run()` | fail on exception |
| `chain/adapter.py` | `run()` | fail_fast |
| `ipi/adapter.py` | `run()` + `wait_for_user` | best_effort |
| `cxp/adapter.py` | `run()` + `wait_for_user` | best_effort |
| `rxp/adapter.py` | `run()` | best_effort |

`ipi`, `cxp`, and `rxp` adapters wrap synchronous entry points via `asyncio.to_thread()`.

---

## Web Server (`server/`)

**Stack:** FastAPI + Jinja2 + HTMX + DaisyUI + WebSockets. Server-rendered HTML fragments — no SPA framework, no build step.

```
Browser ──HTMX──► FastAPI routes ──► Jinja2 templates ──► HTML response
Browser ──WS────► /ws endpoint  ──► ConnectionManager ──► broadcast JSON events
WorkflowRunner.emit() ──► ConnectionManager.broadcast() ──► all connected clients
```

**Key routes:**
- `GET /` — launcher (workflow cards from registry)
- `GET /operations?run_id=` — operations view (DB-driven: status, child runs, findings)
- `GET /research` — research management (runs, findings, targets)
- `GET /settings` — provider credentials, defaults, infrastructure reachability
- `POST /api/workflows/launch` — create target, start workflow as background task, return run_id
- `POST /api/workflows/{run_id}/conclude` — conclude a research campaign (IPI/CXP). Transitions parent run and children in WAITING_FOR_USER to COMPLETED, emits `run_status` WS event, unblocks runner wait event. Idempotent for already-terminal runs.
- `WS /ws` — live workflow events
- `GET /api/providers/{name}/models` — HTMX endpoint returning model area HTML partial. Four states: enumerated (local), curated (cloud), empty (warning), unreachable (error).

**Provider/Model Selector:** Two-step HTMX component (`model_selector.html` + `model_area.html`). Provider dropdown triggers live model fetch via `htmx.ajax()`. Shared across launcher forms and Settings defaults via `{% include %}` with `selector_id` scoping.

**WebSocket event schema:**
```python
{"type": "run_status",  "run_id": str, "status": int, "module": str}
{"type": "progress",    "run_id": str, "message": str}
{"type": "finding",     "finding_id": str, "run_id": str, "module": str, "severity": int, "title": str}
{"type": "waiting",     "run_id": str, "message": str}
{"type": "resumed",     "run_id": str}
{"type": "ipi_hit",     "id": str, "uuid": str, "source_ip": str, "confidence": str, ...}
```

**Run Results Playbook Rendering:** When a run results view loads for an IPI or CXP child run, the `RunGuidance` JSON from the child run's `guidance` column is deserialized and its blocks rendered as a structured deployment playbook. A reusable Jinja2 macro (`partials/guidance_block.html`) renders blocks by `BlockKind` with appropriate table/list/tab styling. Module tabs (`ipi_tab.html`, `cxp_tab.html`) include this macro. Runs with `guidance=None` (legacy) show a muted fallback message.

**IPI Hit Feed Architecture:** The IPI run results view includes a live hit feed:
1. **DB hydration on page load** — `_load_module_data()` queries `ipi_payloads` and `ipi_hits` for the IPI child run and passes them to the template.
2. **Internal HTTP POST bridge** — The IPI callback server (`ipi/server.py`) is a separate process. After `record_hit()`, it POSTs `{"hit_id": "<id>"}` to `POST /api/internal/ipi-hit` on the main server with an `X-QAI-Bridge-Token` header. Aggressive timeout (~1s), single attempt, log-and-continue on failure.
3. **Bridge token auth** — Both processes read `~/.qai/bridge.token` (auto-generated via `core/bridge_token.py`). The internal endpoint validates the header (401 on mismatch).
4. **Notify-by-ID pattern** — The bridge carries only the hit ID; the main server reads the canonical row from the DB and broadcasts an `ipi_hit` WebSocket event.
5. **Client-side dedup** — `ws.js` checks `data-hit-id` attributes before DOM append to avoid duplicating DB-hydrated rows.

**RXP Interpretive Bands:** The RXP results tab displays colored signal badges next to retrieval rate and mean rank metrics. Bands: strong (>=0.7 / <=2.0), borderline (0.3-0.7 / 2.0-5.0), weak (<0.3 / >5.0). These are presentation logic only — no model or DB changes. Each badge includes a tooltip with guidance on what to vary next.

**Adjacent concern:** Inject and audit launcher controls and results drill-down are the next module-specific playbook views (Brief B).

---

## Data Model

Single SQLite database at `~/.qai/qai.db`. Schema V10.

**Shared tables:** `runs`, `targets`, `findings`, `evidence`, `settings`

**Module tables:** `audit_scans`, `inject_results`, `proxy_sessions`, `chain_executions`, `chain_step_outputs`, `ipi_hits`, `ipi_payloads`, `cxp_test_results`, `rxp_validations`

`RunStatus`: PENDING=0, RUNNING=1, COMPLETED=2, FAILED=3, CANCELLED=4, WAITING_FOR_USER=5, PARTIAL=6

`Severity`: INFO=0, LOW=1, MEDIUM=2, HIGH=3, CRITICAL=4

---

## CLI Hierarchy

```
qai
├── audit        scan, enumerate, list-checks, report
├── proxy        start, replay, export, inspect
├── inject       serve, campaign, list-payloads, report
├── chain        run, validate, list-templates, blast-radius, detect
├── ipi          generate, listen, hits, campaigns
├── cxp          build, validate, list-rules, list-formats
├── rxp          validate, list-models, list-profiles
├── runs         list
├── findings     list
├── targets      list, add
├── update-frameworks  check (--atlas for full diff, --no-cache to bypass cache)
└── config       get, set, set-credential, delete-credential, list-providers,
                 import-legacy-credentials
```

---

## Extension Points

### Adding a Scanner (audit)
1. Create `src/q_ai/audit/scanner/{name}.py` implementing `BaseScanner`
2. Implement `async scan(context: ScanContext) -> list[Finding]`
3. Register in `scanner/registry.py`
4. Add `category` entry to `core/data/frameworks.yaml` if new category

### Adding a Payload Template (inject)
Create `src/q_ai/inject/payloads/templates/{name}.yaml` with `name`, `technique`, `tool_name`, `tool_description`, `parameters`, `response`.

### Adding a Chain Template
Create `src/q_ai/chain/templates/{name}.yaml` with `id`, `name`, `category`, `description`, `steps`.

### Adding a CXP Rule
User rules: `~/.qai/cxp/rules/{id}.yaml`. Built-in rules: `src/q_ai/cxp/rules/{id}.yaml`.

### Adding a Workflow
1. Create `src/q_ai/orchestrator/workflows/{name}.py`
2. Add `WorkflowEntry` to `registry.py` with executor assigned
3. Launcher picks it up automatically

---

## Design Decisions

### D16: SARIF is Audit-Only

SARIF output is scoped to the audit module. Inject outcomes use `InjectionOutcome` (not CWE/Rule), and proxy captures are session recordings (not static analysis findings). Normalizing these to SARIF's static analysis schema would require non-trivial refactoring with questionable value. Other modules use JSON bundle export for machine-readable output.

---

## Security Considerations

- All modules test systems the operator owns, controls, or has explicit permission to test.
- API keys stored in OS keyring only — never in config files or shell history.
- Web UI binds to `127.0.0.1` — no authentication, not suitable for network exposure.
- Subprocess commands use list form — no `shell=True`.

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python >=3.11 |
| MCP SDK | mcp |
| LLM abstraction | litellm (behind internal ProviderClient) |
| Web framework | FastAPI + Jinja2 |
| UI | HTMX + DaisyUI + Tailwind CDN |
| Database | SQLite (WAL mode) |
| CLI | Typer |
| Secrets | keyring (OS-native) |
| Testing | pytest + pytest-asyncio |
| Linting | ruff |
| Type checking | mypy |
| Packaging | hatchling + uv |
