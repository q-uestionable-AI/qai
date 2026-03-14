# Contributing to {q-AI}

## Development Setup

```bash
git clone https://github.com/q-uestionable-AI/qai.git
cd qai
uv sync --group dev
uv run pre-commit install
```

## Branch Workflow

**Never commit directly to main.** Branch protection is enforced.

```bash
git checkout main && git pull
git checkout -b feature/your-description
# ... work ...
git push -u origin feature/your-description
```

## Before Committing

1. **Tests pass:** `uv run pytest -q`
2. **Lint clean:** `uv run ruff check .`
3. **Types clean:** `uv run mypy src/`
4. **CLI works:** `uv run qai --help`

Pre-commit hooks enforce linting, formatting, type checking, and secrets detection automatically on each commit.

## Code Standards

- Python >=3.11
- Google-style docstrings on all public functions and classes
- Type hints on all function signatures
- 100 character line length (ruff)
- `async/await` for MCP interactions

## Testing

- Framework: pytest + pytest-asyncio
- Test files mirror source structure under `tests/`

## License

By contributing, you agree that your contributions will be licensed under Apache 2.0.
