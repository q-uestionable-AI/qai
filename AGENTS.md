# {q-AI} — Agent Context

## What This Is

{q-AI} is an offensive security platform for agentic AI infrastructure. It is a Python package
(`q-uestionable-ai` on PyPI) with seven modules, a shared core layer, a CLI (`qai`), and a
local web UI (FastAPI + HTMX + DaisyUI).

**This is a security research tool.** Some code intentionally handles dangerous payloads, injection
techniques, and attack patterns. This is by design — it is an offensive security platform, not a
vulnerable application. Do not "fix" intentional security research patterns.

## Tech Stack

| Component       | Technology                              |
|-----------------|-----------------------------------------|
| Language        | Python >=3.11                           |
| Package Manager | uv (with hatchling build backend)       |
| Web Framework   | FastAPI + Jinja2                        |
| UI              | HTMX + DaisyUI + Tailwind CDN          |
| Database        | SQLite (WAL mode)                       |
| CLI             | Typer                                   |
| Secrets         | keyring (OS-native)                     |
| Testing         | pytest + pytest-asyncio + pytest-timeout|
| Linting         | ruff                                    |
| Type Checking   | mypy (strict mode)                      |
| MCP SDK         | mcp                                     |
| LLM Abstraction | litellm                                 |

## Commands

```bash
# Install dev dependencies (uv is preinstalled on Jules VMs)
uv sync --group dev

# Run tests
uv run pytest

# Run linter
uv run ruff check src/ tests/

# Run formatter check
uv run ruff format --check src/ tests/

# Run type checker
uv run mypy src/

# Run specific test file
uv run pytest tests/path/to/test_file.py -v
```

## Project Layout

```
src/q_ai/
├── core/           # Shared database, models, config, LLM abstraction
├── server/         # FastAPI web UI (Jinja2 templates, HTMX, WebSocket)
├── orchestrator/   # Workflow orchestration engine
├── mcp/            # MCP connection utilities
├── audit/          # MCP server security scanner (10 scanner modules)
├── proxy/          # MCP traffic interceptor
├── inject/         # Tool poisoning and prompt injection testing
├── chain/          # Multi-step attack chain executor
├── ipi/            # Indirect prompt injection payload generation
├── cxp/            # Coding assistant context file poisoning
└── rxp/            # RAG retrieval poisoning measurement

tests/              # Mirrors src/ structure. 1316+ tests.
```

## Code Standards

- **Line length:** 100 characters
- **Docstrings:** Google-style (Args, Returns, Raises) on all public functions and classes
- **Type annotations:** Required on all public function signatures (`disallow_untyped_defs = true`)
- **Async:** MCP SDK is async-native. Scanner modules and client use `async/await`
- **Subprocess:** Always use list form, never `shell=True`
- **Paths:** Always use `pathlib.Path`, never hardcoded separators
- **Imports:** Security-related imports (hashlib, subprocess, etc.) use `# noqa: S` when safe

## Important Constraints

- **Cross-platform:** Must work on Windows, macOS, and Linux. No platform-specific shell commands.
- **No new dependencies** without explicit approval.
- **Web UI binds to 127.0.0.1 only** — no authentication, not for network exposure.
- **API keys** go in OS keyring only, never config files or environment variables in code.
- **Tests must pass on all platforms.** Mark platform-specific tests with `@pytest.mark.skipif`.

## What NOT to Touch

- Do not modify `pyproject.toml` version, dependencies, or build config
- Do not modify `.github/workflows/` CI configuration
- Do not modify `CLAUDE.md` (local-only file for Claude Code)
- Do not modify `DEVELOPMENT_WORKFLOW.md`
- Do not restructure the module layout or rename modules
- Do not "fix" intentional offensive security patterns (payload generation, injection techniques,
  malicious MCP server construction, etc.)
