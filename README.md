# {q-AI}

[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![CI](https://github.com/q-uestionable-AI/qai/actions/workflows/ci.yml/badge.svg)](https://github.com/q-uestionable-AI/qai/actions/workflows/ci.yml)
[![CodeQL](https://github.com/richardspicer/mutual-dissent/actions/workflows/codeql.yml/badge.svg)](https://github.com/richardspicer/mutual-dissent/actions/workflows/codeql.yml)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

[![Docs](https://img.shields.io/badge/docs-q--uestionable.ai-8b5cf6)](https://docs.q-uestionable.ai)

**Security testing for agentic AI.**

- Audit MCP servers
- Intercept agent traffic
- Test tool poisoning and prompt injection
- Execute multi-step attack chains
- Generate IPI payloads
- Poison coding assistant context files
- Measure RAG retrieval rank.

Local web UI orchestrates multi-module workflows.
All findings stored in a SQLite database.

> Research program by [Richard Spicer](https://richardspicer.io) · [{q-AI}](https://q-uestionable.ai)

---

## Framework Coverage

All audit findings map to four security taxonomies:

| Framework | Coverage |
|-----------|----------|
| [OWASP MCP Top 10](https://owasp.org/www-project-mcp-top-10/) | All 10 categories |
| [OWASP Agentic Top 10](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) | All 10 categories |
| [MITRE ATLAS](https://atlas.mitre.org/) | Technique-level mapping per finding category |
| [CWE](https://cwe.mitre.org/) | Weakness-level mapping per finding category |

---

## Install

```bash
pip install q-uestionable-ai
```

Or from source:

```bash
git clone https://github.com/q-uestionable-AI/qai.git
cd qai
uv sync --group dev
```

---

Full documentation at [docs.q-uestionable.ai](https://docs.q-uestionable.ai)

---

## Legal

All tools are intended for authorized security testing only. Only test systems you own, control, or have explicit permission to test. Responsible disclosure for all vulnerabilities discovered.

## License

[Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)

## AI Disclosure

This project uses a human-led, AI-augmented workflow. See [AI-STATEMENT.md](AI-STATEMENT.md)
