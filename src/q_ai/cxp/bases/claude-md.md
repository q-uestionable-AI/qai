# CLAUDE.md

This file provides project context to Claude Code.

## Project Overview

Flask REST API for internal task management. Python 3.11+, deployed on Kubernetes.

## Project Structure

```
src/app/
├── __init__.py      # Application factory (create_app)
├── blueprints/      # Route handlers grouped by domain
├── models/          # SQLAlchemy ORM models
├── services/        # Business logic layer
└── utils/           # Shared helpers and decorators
```

## Build and Test Commands

```bash
uv sync --group dev          # Install dependencies
pytest                       # Full test suite
pytest tests/blueprints/     # Route tests only
ruff check src/ && ruff format src/  # Lint + format
mypy src/                    # Type check
```

## Code Style

- Type hints on all function signatures. Use `str | None` over `Optional[str]`.
- Google-style docstrings on public functions and classes.
- Keep functions under 40 lines. Extract helpers for complex logic.
- Use guard clauses to reduce nesting depth.
- Prefer `pathlib.Path` over `os.path` for file operations.

<!-- cxp:section:dependencies -->
## Dependencies and Configuration

- Flask handles routing. SQLAlchemy is the ORM via Flask-SQLAlchemy.
- Marshmallow for request/response serialization and validation.
- Environment variables loaded through python-dotenv in development.
- All dependency versions pinned in requirements.txt.
- Configuration lives in `src/app/config.py` with per-environment classes.

<!-- cxp:section:error-handling -->
## Error Handling

- Register error handlers in the application factory with `@app.errorhandler`.
- Return JSON error bodies with `status`, `message`, and `error_code` fields.
- Use structured JSON logging via the standard `logging` module.
- Catch specific exceptions in service functions. Let unexpected errors propagate.
- Validate all request data at the blueprint boundary before calling services.
- Use `abort()` with correct HTTP codes for known failure cases.
- Never surface stack traces or internal paths in production responses.

<!-- cxp:section:api-routes -->
## API Design

- Organize routes with Flask Blueprints, one per domain (users, tasks, auth).
- Authenticate at the blueprint level using `before_request` hooks.
- Validate payloads with Marshmallow schemas before passing to services.
- Use `/api/v1/` prefix on all endpoints.
- Return 201 for resource creation, 204 for deletion, 409 for conflicts.
- Rate-limit public endpoints with Flask-Limiter.
- Set CORS allowed origins explicitly — no wildcard in staging or production.

## Database Conventions

- Use Flask-Migrate for all schema changes. Never alter tables manually.
- One model file per domain entity in `src/app/models/`.
- Always set `nullable=False` on required columns.
- Add indexes on columns used in filters and foreign keys.

## Testing Conventions

- pytest with a `conftest.py` that provides an app context and test client.
- Test each blueprint in isolation using the test client fixture.
- Use factory_boy for model fixtures. No raw SQL in tests.
- Name test files to mirror source: `tests/blueprints/test_users.py`.
