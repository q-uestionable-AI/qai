# {q-AI}

[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![CI](https://github.com/q-uestionable-AI/qai/actions/workflows/ci.yml/badge.svg)](https://github.com/q-uestionable-AI/qai/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Docs](https://img.shields.io/badge/docs-q--uestionable.ai-8b5cf6)](https://docs.q-uestionable.ai)

**Offensive security platform for agentic AI infrastructure.**

Audit MCP servers, intercept agent traffic, test tool poisoning and prompt injection, execute multi-step attack chains, generate IPI payloads, poison coding assistant context files, and measure RAG retrieval rank. A local web UI orchestrates multi-module workflows. All findings write to a shared SQLite database.

> Research program by [Richard Spicer](https://richardspicer.io) · [{q-AI}](https://q-uestionable.ai)

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

RXP requires optional dependencies:

```bash
pip install "q-uestionable-ai[rxp]"
```

---

## Usage

```bash
# Start the web UI (opens browser)
qai

# Audit — scan an MCP server against the OWASP MCP Top 10
qai audit scan --transport stdio --command "npx @modelcontextprotocol/server-everything"

# Proxy — intercept MCP traffic
qai proxy start --transport stdio --target-command "python my_server.py"

# Inject — run a tool poisoning campaign against any LLM provider
qai inject campaign --model anthropic/claude-sonnet-4-20250514
qai inject campaign --model openai/gpt-4o
qai inject campaign --model ollama/llama3

# Chain — execute multi-step attack chains
qai chain list-templates
qai chain run --chain-file chain.yaml --dry-run

# IPI — generate indirect prompt injection payloads
qai ipi generate --callback-url http://localhost:8080 --format pdf --output ./payloads/

# CXP — build poisoned coding assistant context repos
qai cxp build --format cursorrules --output ./test-repos/

# RXP — measure RAG retrieval rank of adversarial documents
qai rxp validate --profile rag-security --model minilm-l6
```

Full documentation at [docs.q-uestionable.ai](https://docs.q-uestionable.ai).

---

## Legal

All tools are intended for authorized security testing only. Only test systems you own, control, or have explicit permission to test. Responsible disclosure for all vulnerabilities discovered.

## License

[Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0)

## AI Disclosure

This project uses a human-led, AI-augmented workflow. See [AI-STATEMENT.md](AI-STATEMENT.md).
