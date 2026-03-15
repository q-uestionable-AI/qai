# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in {q-AI}, please report it responsibly.

**Preferred:** Use [GitHub Private Vulnerability Reporting](https://github.com/q-uestionable-AI/qai/security/advisories/new) — click "Report a vulnerability" in the Security tab. This keeps coordination on-platform and follows the [OpenSSF Vulnerability Disclosure Guide](https://github.com/ossf/oss-vulnerability-guide).

**Alternative:** Email **security@q-uestionable.ai** with a description of the vulnerability, steps to reproduce, and potential impact assessment.

1. **Do not** open a public GitHub issue for security vulnerabilities
2. Allow up to 72 hours for initial response
3. We will coordinate disclosure timeline with you

## Scope

{q-AI} is a security testing tool. Vulnerabilities in the tool itself (not in targets being tested) are in scope:

- Command injection in CLI argument handling
- Credential leakage in reports, logs, or the web UI
- API key exposure via the keyring integration or config files
- Dependency vulnerabilities with exploitable paths
- Unsafe deserialization of scan results, MCP messages, or campaign data
- Web UI vulnerabilities (SSRF via infrastructure health checks, path traversal in file outputs)

Out of scope: vulnerabilities in third-party MCP servers or AI systems discovered *by* the tool — those should be reported to the relevant vendor.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |
