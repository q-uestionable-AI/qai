<claude_config>
<guardrails>
  <boundaries>
    <do_not_edit></do_not_edit>
    <do_not_create>plans/*, design_docs/*, */.plan</do_not_create>
    <rule>Do not create PRs. Push the branch and stop. The developer creates PRs manually.</rule>
    <rule>Do not attempt to install CLI tools (gh, hub, etc.).</rule>
    <rule>NEVER commit plan files. If subagent-driven development requires a plan file, write it strictly to the OS temp directory (/tmp on Unix, or TMPDIR/TEMP env var on Windows), not the repo. Plans are transient session artifacts, not project documentation.</rule>
  </boundaries>

  <verification_scope>
    <rule>Run this exact command on new/changed files before committing: `uv run ruff check . && uv run ruff format --check . && uv run mypy src/q_ai/ && uv run pre-commit run --all-files`</rule>
    <rule>Test scope is specified in the task brief. Follow it exactly.</rule>
    <rule>If no brief is present, default to scoped (run only tests for new/changed code).</rule>
    <rule>Smoke test the CLI after changes (`qai --help`).</rule>
    <rule>CI always runs the full suite on every PR regardless.</rule>
  </verification_scope>

  <failure_policy>
    <rule>If verification hits a problem you cannot resolve in 2 attempts, commit the work to the branch and report what failed.</rule>
    <rule>Do not spin or loop on the same failure.</rule>
  </failure_policy>

  <timeout_policy>
    <rule>If any test run exceeds 30 seconds, stop and identify the stuck test.</rule>
    <rule>Do not set longer timeouts and wait — diagnose instead.</rule>
    <rule>Before running tests, kill any orphaned python/node processes from previous runs.</rule>
    <rule>After killing a stuck process, clean up zombies before retrying.</rule>
  </timeout_policy>
</guardrails>

<project_overview>
  q-ai is a unified offensive security platform for agentic AI infrastructure.

  Seven modules under one package:
  - audit: MCP server scanning
  - proxy: MCP traffic interception
  - inject: tool poisoning
  - chain: attack path composition
  - ipi: indirect prompt injection
  - cxp: context file poisoning
  - rxp: RAG retrieval poisoning

  Local web UI (FastAPI + HTMX) as primary workflow interface.
  CLI as peer interface.
  Unified SQLite database at ~/.qai/qai.db.
</project_overview>

<package_layout>
  src/q_ai/
  ├── __init__.py
  ├── __main__.py
  ├── cli.py              # Root Typer app
  ├── core/               # Phase 2: shared DB, models, config, frameworks
  ├── server/             # Phase 3: FastAPI web UI
  ├── orchestrator/       # Phase 5: workflow engine
  ├── audit/              # Phase 4a: MCP server scanning
  ├── proxy/              # Phase 4c: MCP traffic interception
  ├── inject/             # Phase 4b: tool poisoning
  ├── chain/              # Phase 4d: attack path composition
  ├── ipi/                # Phase 4e: indirect prompt injection
  ├── cxp/                # Phase 4f: context file poisoning
  └── rxp/                # Phase 4g: RAG retrieval poisoning
</package_layout>

<coding_standards>
  <rule>Python: >=3.11</rule>
  <rule>Docstrings: Google-style on all public functions and classes</rule>
  <rule>Async: MCP SDK is async-native. Use `async/await` for MCP interactions</rule>
  <rule>Type hints: Required on all function signatures</rule>
  <rule>Line length: 100 chars (ruff)</rule>
  <rule>Imports: Sorted by ruff (isort rules)</rule>
  <rule>Cross-platform: Windows + macOS + Linux — use pathlib, no platform-specific shell commands</rule>
</coding_standards>

<code_quality>
  <rule>Functions: max 50 lines of logic (excluding docstring and type stubs). If longer, decompose.</rule>
  <rule>One responsibility per function. If you need "and" to describe what it does, split it.</rule>
  <rule>Guard clauses: return/raise early on error conditions. Do not nest the happy path inside conditionals.</rule>
  <rule>No magic strings or numbers in logic. Define constants at module level or use enums.</rule>
  <rule>SQL: Always use parameterized queries (? placeholders). Never interpolate values into SQL strings.</rule>
  <rule>Empty collection guard: Before using a collection in SQL IN (...) or iteration, check if empty and handle the empty case explicitly.</rule>
  <rule>Context managers: Use `with` for all file handles, DB connections, and anything requiring cleanup.</rule>
  <rule>Don't suppress errors silently. If catching an exception to continue, log the exception or comment why.</rule>
  <rule>Prefer composition over nesting. Max 3 levels of indentation inside a function body.</rule>
  <rule>Explicit over implicit (PEP 20). If behavior depends on a default value, state the default in the call.</rule>
</code_quality>

<testing_protocols>
  <framework>pytest + pytest-asyncio (asyncio_mode = "auto") + pytest-timeout (30s)</framework>
  <execution>uv run pytest -q</execution>
  <layout>Test files mirror source layout under tests/</layout>
</testing_protocols>

<git_workflow>
  <rule>Feature branches required for code changes (feature/*, fix/*).</rule>
  <rule>Doc and config-only changes may be pushed directly to main.</rule>
  <rule>Before writing any code, check the current branch with `git branch --show-current`. If on main, create and switch to a feature branch.</rule>
  <rule>End of Session: Commit to branch, `git stash -m "description"`, or `git restore .` — never leave uncommitted changes.</rule>
  <shell_quoting_critical>
    CMD corrupts `git commit -m "message with spaces"`. Always use:
    `echo "feat: description here" > .commitmsg && git commit -F .commitmsg && del .commitmsg`
  </shell_quoting_critical>
</git_workflow>

<environment>
  <dependencies>Managed via `uv` with PEP 735 dependency groups. Sync with: `uv sync --group dev` (Without --group dev, dev dependencies get stripped).</dependencies>
  <build>src/ layout with hatchling backend. Entry point: qai = "q_ai.cli:app"</build>
</environment>

<legal_and_ethical>
  <rule>Only test systems you own, control, or have explicit permission to test.</rule>
  <rule>Responsible disclosure for all vulnerabilities.</rule>
  <rule>Frame all tooling as defensive security testing tools.</rule>
</legal_and_ethical>
</claude_config>
