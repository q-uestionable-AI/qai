# {q-AI} — Roadmap

## Problem Statement

AI agents are being deployed with broad access to tools, data, and systems — but the security
tooling hasn't kept pace. MCP adoption is accelerating (Claude, ChatGPT, Cursor, Gemini, VS Code)
while most organisations have no visibility into what their MCP servers expose or how agents
behave when tools are manipulated.

Existing tools (Garak, PyRIT) focus on LLM output analysis — they test what a model says, not
what an agent does. {q-AI} tests agent infrastructure: MCP servers, tool trust boundaries,
content ingestion pipelines, coding assistant context files, and retrieval systems. The distinction
matters because agent-level attacks produce real-world effects — code execution, data exfiltration,
lateral movement — that output-level analysis doesn't catch.

### Competitive Landscape

| Tool | Approach | {q-AI} Differentiator |
|------|----------|----------------------|
| Garak (NVIDIA) | LLM output vulnerability scanning | Tests agent infrastructure, not model outputs |
| PyRIT (Microsoft) | Multi-turn red teaming | Maps to OWASP MCP Top 10, tests MCP protocol |
| Invariant MCP-Scan | CLI scanner for tool description attacks | Full OWASP MCP Top 10 + multi-module kill chain |
| ScanMCP.com | Cloud-based scanner | Open source, local execution, SARIF for CI/CD |
| Equixly CLI | Commercial MCP testing | Free, community-extensible |
| MCP Guardian (EQTY Lab) | Runtime security proxy | Complementary — guardian is runtime defence, {q-AI} is pre-deployment offence |

---

## Phased Delivery

### Phase 1–4: Foundation ✅

All seven modules ported and functional. Shared SQLite database with parent/child run lineage.
Local web UI (FastAPI + HTMX). CLI with module subcommands. 1278 tests.

### Phase 5: Workflow Orchestration ✅

"Assess an MCP Server" workflow: audit → proxy (background) + inject, end-to-end through the
browser. Provider-agnostic injection (litellm, any provider/model). Keyring-based credential
storage. Live WebSocket updates. Module adapters for all seven modules.

### Remaining Workflows

Five workflows are visible in the launcher and will be implemented after Phase 6.
Module adapters for all five already exist (Phase 5e).

| Workflow | Modules | Notes |
|----------|---------|-------|
| Test Document Ingestion | ipi, rxp | Generate payloads, validate retrieval rank pre-deployment. RXP is optional pre-validation. |
| Test a Coding Assistant | cxp | Build poisoned repo, guided manual steps, record results |
| Trace an Attack Path | chain | Execute chain fail_fast, step-by-step evidence across trust boundaries |
| Measure Blast Radius | chain | Analysis-only, depends on chain execution results |
| Manage Research | all | Cross-module research view — largely functional from Phase 3 |

### Phase 6: Public Launch

**Goal:** The repo is public, the package is on PyPI, and old repos are transitioned.

**Sequence:**
1. Final repo review — README rewrite, SECURITY.md, responsible use policy, CI green
2. Flip `q-uestionable-AI/qai` to public
3. Publish `q-uestionable-ai` to PyPI (first release)
4. Deprecation releases for `counteragent` and `countersignal` — CLI warning + README banner
5. Transfer `q-uestionable-AI/counteragent` → `richardspicer/counteragent`, same for countersignal
6. Unified Mintlify docs at `docs.q-uestionable.ai`
7. q-uestionable.ai site update
8. Announcement post on richardspicer.io

**Done when:** `pip install q-uestionable-ai` works, repo is public, old repos redirect.

### Phase 7: Remaining Workflows

Five workflows are stubbed in the launcher but not implemented. Adapters exist for all modules.

| Workflow | Modules | Notes |
|----------|---------|-------|
| Test Document Ingestion | ipi, rxp | Generate payloads, validate retrieval before deployment |
| Test a Coding Assistant | cxp | Build poisoned repo, guided manual steps, record results |
| Trace an Attack Path | chain | Execute chain, fail_fast, step-by-step evidence |
| Measure Blast Radius | chain | Analysis-only, depends on chain execution results |
| Manage Research | all | Cross-module research view — largely functional from Phase 3 |

**Done when:** All five workflows launchable from the browser with correct module orchestration.

### Phase 8: Research Validation and v1.0

**Goal:** Interface stability commitment backed by real research evidence.

See [v1.0 Exit Criteria](#v10-exit-criteria) below.

---

## Planned Directions (Post-v1.0)

### Public API & MCP Server

Expose qai's capabilities programmatically. Enables CI/CD security gates, SIEM/ticketing integration, scripted research campaigns, lab automation, and AI agent access.

**Sequence:** Service layer extraction → API v1 (read) → API v1 (write + auth) → Headless mode → MCP server v1 → Dogfooding.

The API is a versioned `/api/v1/` JSON surface backed by a shared service layer that all interfaces (UI, CLI, API, MCP) call. The MCP server is a thin adapter over the API — it does not invent new semantics. stdio transport first. Read-only by default with opt-in scan capability behind full guardrail set (target allowlist, wait_for_user with canonical approval summary, parameter normalization, audit logging).

**Plan:** `Plans/api-mcp-server-plan.md`
**RFC:** `Plans/api-mcp-server-rfc.md`
**Concept:** `Plans/api-mcp-server-concept.md`

### Desktop Application Packaging

Package qai as a double-clickable desktop app on Windows, macOS, and Linux. pywebview native window + PyInstaller. No terminal, proper lifecycle management, single-instance behavior, localhost session hardening.

**Plan:** `Plans/desktop-packaging-plan.md`
**RFC:** `Plans/desktop-packaging-rfc.md`

---

## v1.0 Exit Criteria

SemVer 1.0 is a public commitment to interface stability. After 1.0, breaking changes to CLI
commands, report formats, data models, or YAML schemas require a major version bump.

### Capability Readiness

| Module | Gate |
|--------|------|
| audit | ≥3 distinct MCP server targets scanned, findings documented in Research Log |
| proxy | ≥1 end-to-end intercept session captured and replayed against a live MCP server |
| inject | ≥1 campaign run against a real LLM endpoint with scoring results documented |
| chain | ≥1 chain executed end-to-end against real targets with step evidence documented |
| ipi | ≥1 payload document triggering a callback from a target platform |
| cxp | ≥1 hit confirmed against a real coding assistant |
| rxp | ≥1 validated retrieval rank measurement against a real embedding model |

### Research Validation

- ≥1 publishable finding from inject, chain, ipi, or cxp campaigns (finding ID in Findings/)
- OWASP MCP Top 10 mapping validated against real scan results

### Interface Stability

**CLI commands** — frozen at 1.0 for all implemented subcommands. Flags and required arguments stable.

**Data models** — `Run`, `Target`, `Finding`, `Evidence`, `Severity`, `RunStatus` frozen.
Module-specific models frozen when their v1.0 gate is met.

**Report formats** — HTML, SARIF, JSON scan reports; inject YAML payload schema; chain YAML
template schema. Breaking changes require major version bump after 1.0.

**Protocols** — `ProviderClient` protocol interface frozen. `BaseScanner` ABC frozen.
`TransportAdapter` protocol frozen.

### Explicitly Post-1.0

- Proxy client-facing HTTP adapters (proxy as standalone network service)
- Chain `blast-radius` and `detect` command implementations (stubs ship at v1.0)
- Multi-model comparison in RXP
- Detection rule export automation

---

## What Success Looks Like

- A CVE or bounty finding with a published finding doc and Sigma/Wazuh detection rules
- Detection rules that other defenders actually deploy
- A tool that security researchers use for real MCP assessments
- Conference CFP accepted (DEF CON AI Village, USENIX, etc.)

---

## Framework Mapping

| Framework | Usage |
|-----------|-------|
| OWASP MCP Top 10 | Primary vulnerability taxonomy for all scanner categories (all 10 categories mapped, verified 2026-03-18) |
| OWASP Top 10 for Agentic AI | Agentic attack classification — all 10 ASI categories mapped |
| MITRE ATLAS | Adversarial ML technique mapping — all 10 categories mapped, verified against ATLAS.yaml v5.4.0 |
| CWE | Weakness enumeration for SARIF consumers and security tooling — all 10 categories mapped |

Framework mappings are maintained in `src/q_ai/core/data/frameworks.yaml` and kept current via `qai update-frameworks`.

---

## Out of Scope (for now)

- **Cloud-hosted version.** {q-AI} is a local lab tool. No SaaS, no multi-tenant, no hosted scanning.
- **GUI installer.** ~~Target audience is security researchers comfortable with Python tooling.~~ Desktop application packaging is now a planned direction — see `Plans/desktop-packaging-plan.md`.
- **Real-time collaboration.** Single-operator tool. Research sharing happens via exported findings and published reports.
- **LLM output testing.** That's Garak's problem. {q-AI} tests infrastructure and agent behaviour, not model outputs.

---

## Reference Links

- OWASP MCP Top 10: https://owasp.org/www-project-mcp-top-10/
- OWASP Top 10 for Agentic AI: https://genai.owasp.org/
- MCP Specification: https://modelcontextprotocol.io/
- MITRE ATLAS: https://atlas.mitre.org/
- Garak: https://github.com/NVIDIA/garak
- PyRIT: https://github.com/Azure/PyRIT
