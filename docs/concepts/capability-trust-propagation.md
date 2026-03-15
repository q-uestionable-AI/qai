# Capability Trust Propagation Failure

**Status:** Research theme — informs chain, audit, and inject development  
**Scope:** Cross-module

---

## The Problem

Security review of MCP ecosystems tends to focus on individual components: is this tool
poisoned? Is this resource leaking context? Is this token scoped correctly?

That component-level lens catches real bugs, but it misses a failure class that emerges from
composition. The failure is not that any single tool or resource is broken. The failure is
that **trust level, provenance, and authorization meaning decay as data moves between
components** — and nothing in the system explicitly tracks or enforces those semantics across
hops.

This document names that failure class **Capability Trust Propagation Failure**.

---

## What Trust Propagation Means

In any multi-tool agent workflow, data carries implicit trust properties at each hop:

- **Provenance:** Where did this data originate? A user input? A retrieved resource? An
  untrusted external API? A prior tool's output?
- **Integrity level:** How much should the system trust this data to be accurate, unmodified,
  and non-malicious?
- **Authorization scope:** What actions is this data entitled to trigger? Was it produced
  under user-approved scope, or did it arrive through an unapproved path?

In a well-designed system, these properties would be tracked and enforced at each transition.
In practice, they are almost never tracked. The model, the client, and the broker treat data
as undifferentiated text — a tool's output looks the same regardless of whether it came from
a read-only file listing or a privileged admin API.

**Trust propagation failure occurs when data is silently promoted** — when it crosses a
trust boundary without any mechanism checking whether that promotion is legitimate.

---

## Why This Is Distinct from Existing Categories

The OWASP MCP Top 10 covers related territory across several categories (MCP01–MCP10).
Each captures a component of the failure, but none isolates the **composition problem itself**.

The gap is not another injection variant. The gap is:

> How does the system preserve provenance and trust boundaries across multi-step agent
> behavior?

This is closer to an insecure design issue (OWASP A06) than a single injection or auth bug.
The flaw is in how the system permits unwanted state changes and unsafe trust transitions —
not in one bad input validator or one poisoned tool definition.

---

## Concrete Failure Patterns

### Pattern 1: Untrusted context becomes trusted tool input

A resource is retrieved from an external source (low integrity). The model incorporates it
into reasoning. A subsequent tool call uses content derived from that resource as a parameter.
The tool treats its input as model-endorsed, not externally-sourced. Provenance is lost at
the tool invocation boundary.

**Trust transition:** External content → model context → tool parameter

### Pattern 2: Read-only output becomes action-authorizing evidence

A read-only tool (file listing, log viewer, status check) returns output that includes text
resembling instructions, credentials, or authorization tokens. A subsequent tool — or the
model's reasoning — treats that output as evidence that an action is approved. Read-only
semantics are not propagated to consumers.

**Trust transition:** Read-only tool output → model reasoning → write/execute tool input

### Pattern 3: User-approved scope silently expands

A user approves a specific action ("read my calendar"). The tool returns data. That data
informs a subsequent tool call outside the original approval scope ("send an email based on
calendar content"). The authorization boundary of the original approval is not enforced on
downstream actions.

**Trust transition:** User-approved read → model reasoning → unapproved write

### Pattern 4: Low-integrity source influences high-impact action

A tool returns data from a source with no integrity guarantees (web scrape, user-contributed
content, cached third-party response). That data influences a high-impact tool invocation
(database write, payment API, infrastructure change). No policy checkpoint exists between
the low-integrity source and the high-impact action.

**Trust transition:** Low-integrity data → model decision → high-impact tool invocation

---

## The Worm-Class Hypothesis

The patterns above describe trust decay within a single workflow. But MCP ecosystems also
have **writable shared context** — memory stores, shared resources, persistent artifacts,
configuration files, downstream services.

When trust propagation failure combines with persistent writable context, the failure mode
escalates from "one workflow produces an unsafe outcome" to "compromise self-propagates
across agents and sessions without human re-authorization."

A propagation path:

1. Attacker plants malicious instruction in a resource, tool description, or shared context
2. Agent retrieves and treats it as operationally relevant (no provenance check)
3. Content induces a tool call — writing to another shared store or creating an artifact
4. That artifact becomes fresh context for another agent, tenant, workflow, or future session
5. The malicious instruction has a new foothold without direct attacker interaction

Each individual step maps to an existing OWASP MCP category. The worm-class behavior
emerges from composition — specifically from trust propagation failure combined with
persistent writable context.

The research question:

> Can an attacker cause untrusted content to be re-emitted by one agent into a place where
> another agent will later consume it as trusted or semi-trusted context?

---

## The Capability Graph Model

Rather than framing chain attacks as sequential execution, the stronger model is
**capability graph analysis**:

- **Nodes:** Tools, resources, prompts, and authorization endpoints
- **Edges:** Data flows — tool output feeding tool input, resource content entering model
  context, model reasoning triggering tool invocation
- **Trust labels on edges:** Provenance, integrity level, and authorization scope of data
  as it moves along each edge

The chain module's role under this model:

1. **Map the graph** — enumerate nodes and edges from the target MCP environment
2. **Label trust levels** — assign provenance and integrity properties to each edge
3. **Identify promotion points** — find edges where data crosses a trust boundary without
   an explicit policy checkpoint
4. **Test promotion** — exercise those edges to determine whether trust semantics are
   enforced or silently promoted

This reframes chain from "attack sequencer" into "trust boundary analyzer."

---

## Module Mapping

| Module | Role in trust propagation research |
|--------|-----------------------------------|
| **audit** | Detect trust-surface exposures: tool metadata deception, resource context manipulation, shadow tool detection |
| **chain** | Map capability graph, label trust transitions, test promotion points across multi-step execution |
| **inject** | Deliver payloads at specific graph nodes to test whether trust labels are enforced downstream |
| **ipi** | Generate content designed to survive trust handoffs in document ingestion pipelines |
| **cxp** | Test trust semantics in coding assistant context file consumption |

---

## Detection Engineering

Every publishable finding requires detection artifacts. Trust propagation failures have
specific signatures:

- **Provenance loss** — tool output consumed by another tool without provenance metadata
- **Trust boundary crossing** — low-integrity data appearing in a high-impact tool's input
- **Scope expansion** — actions exceeding the scope of the most recent user approval
- **Silent promotion** — data that changes trust context without triggering a policy
  checkpoint

These translate to Sigma rules, Wazuh detection rules, and Semgrep patterns for MCP client
implementations. Detection artifacts publish to [mlsecopslab.io/research](https://mlsecopslab.io/research) alongside findings.

---

## Publication Positioning

The strongest angle:

> Current OWASP MCP categories capture individual failure components, but an emergent failure
> class arises from their composition: trust semantics decay across multi-step agent workflows.
> Capability Trust Propagation Failure is a cross-cutting analysis lens, with a testing
> methodology based on capability graph analysis with trust labeling, demonstrated against
> real MCP client implementations.

This is adjacent to and supportive of OWASP MCP Top 10, not competitive. Concrete and
testable, not theoretical. Grounded in tool-backed evidence, accompanied by detection
engineering artifacts.

---

## Relationship to Current Work

This research theme extends shipped functionality — it is not a separate track:

- **chain** — Sequential execution model already tracks trust boundaries. Trust propagation
  adds capability graph mapping and trust labeling as an analytical layer over existing
  execution infrastructure.
- **inject** — Payload library and campaign infrastructure are the foundation. Trust
  propagation payloads are a future payload family.
- **audit** — Trust audit checks (tool trust, resource trust, chain checks) are a future
  enhancement to the scanner catalog.
