# q-AI — Architecture

## Purpose

This document records durable architectural invariants for q-AI. It is
intentionally about system shape, boundaries, and operating model rather than
route inventories, per-file ownership maps, or refactor planning.

## System Shape

q-AI is a local Python application with two operator entry points:

- the `qai` CLI
- a localhost web UI launched by `qai ui`

Its core security-testing behavior is implemented in peer domain modules:

| Module | Stable responsibility |
| --- | --- |
| `audit` | Scan MCP servers for security findings |
| `proxy` | Intercept and record MCP traffic |
| `inject` | Run tool-poisoning and prompt-injection campaigns |
| `ipi` | Generate indirect prompt-injection payloads and track hits |
| `cxp` | Build poisoned context-file test repos for coding assistants |
| `rxp` | Measure retrieval poisoning behavior |
| `chain` | Compose multi-step attack paths across modules |

Those modules are supported by shared layers:

- `core` for shared models, persistence, configuration, and provider abstractions
- `mcp` for MCP transport/session handling
- `orchestrator` for workflow composition and run coordination
- `services` for shared query/read paths over persisted data
- `server` for the local web UI
- `assist` for operator guidance and interpretation
- `imports` for normalizing external tool results into q-AI's data model

The important architectural fact is that q-AI is not built around a single
feature surface. It is a set of security-testing modules that share one
backbone and one persistence model.

## Shared Backbone

The backbone of q-AI is the shared run model stored in a local SQLite database.
The common schema centers on `targets`, `runs`, `findings`, `evidence`, and
`settings`, with additional module-specific tables for execution data such as
scan results, proxy sessions, chain executions, generated payloads, and
retrieval-validation output.

`WorkflowRunner` turns multi-step operations into a parent workflow run plus
child module runs. Progress events, findings, completion state, and
human-in-the-loop pauses all travel through that same run lifecycle. Modules
that pause for operator action do so through `WAITING_FOR_USER` / resume
transitions instead of inventing a separate state system.

Imported external-tool results are normalized into the same run/finding/evidence
model. That keeps native results and imported results queryable through the same
shared persistence layer.

## Boundaries and Responsibilities

### Core Boundary

`core` owns the durable cross-cutting contracts: database access, schema
migration, shared data models, configuration, credential lookup, provider/model
abstractions, and run guidance persistence. It is the layer other parts of the
system converge on instead of each module inventing its own storage or provider
contract.

### MCP Boundary

`mcp` centralizes client connections to MCP servers over stdio, SSE, and
streamable HTTP. Modules that talk to MCP servers build on that shared async
connection boundary rather than embedding transport setup into the web layer.

### Orchestration Boundary

`orchestrator` owns workflow registration, parent/child run coordination,
progress/finding event emission, and resume gates. Module adapters are the seam
between workflow execution and module-specific engines; they let workflows
compose modules without moving module logic into the web server.

### Query Boundary

`services` provides shared read/query helpers over persisted runs, findings, and
evidence. Route handlers and other callers reuse those query paths instead of
re-implementing database reads independently.

### Operator Surfaces

`server` provides the local web UI with FastAPI, Jinja templates, static assets,
and WebSocket event fan-out. `cli.py` provides the command-line entry point and
launches the web UI on `127.0.0.1`. The browser surface and CLI are operator
interfaces over the same backend model, not separate products with separate
state.

## LLM and Assistant Boundary

q-AI keeps its model-provider boundary in shared core code. `core.llm` defines
the provider-agnostic protocol and normalized response types.
`core.providers` holds provider/model registry information and model-discovery
logic. The current LiteLLM-backed implementation is housed in
`core.llm_litellm.py`.

The stable architectural point is the boundary, not the current library choice:
provider-facing code is isolated behind shared interfaces instead of being
spread across modules.

The assistant is an operator-aid layer on top of that boundary. It builds a
local knowledge index, retrieves product and user reference material, and
assembles prompts that distinguish trusted product content, user-provided
reference material, and untrusted scan-derived content. It helps operators
navigate and interpret q-AI; it is not the platform backbone and does not own
workflow orchestration, persistence, or module composition.

## Security and Trust Invariants

- `qai ui` binds the web UI to `127.0.0.1`, making the browser surface a local
  operator interface rather than a network service.
- Non-secret settings and credentials are handled through separate paths:
  ordinary settings live in config/database stores, while credential lookup is
  resolved separately by `core.config`.
- Assistant prompt assembly treats target-derived scan content as untrusted data
  and delimits it accordingly instead of treating it as instructions.
- IPI bridge communication uses a dedicated token file for internal
  server-to-server authentication.

## What This Document Intentionally Omits

This document does not try to freeze the current FastAPI route surface, template
inventory, or file-by-file ownership map. Those details change faster than the
architectural boundaries above and should be verified in source when needed.
