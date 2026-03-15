# AGENTS.md

## Sandbox Environment

This project runs in a sandboxed environment with network access disabled.
All dependencies are pre-installed. Do not attempt to install packages or
fetch remote resources during task execution.

## Project Context

Flask REST API for internal task management. Python 3.11+, SQLAlchemy ORM,
Marshmallow for validation. Application factory pattern in `src/app/__init__.py`.

## Task Workflow

1. Read the task description and identify which files need changes.
2. Check existing patterns in related files before writing new code.
3. Make changes in the `src/app/` directory. Follow existing conventions.
4. Run `pytest` to verify changes. Fix failures before completing.
5. Run `ruff check src/` to catch lint issues.

## File Locations

- Routes: `src/app/blueprints/` — one file per domain
- Models: `src/app/models/` — SQLAlchemy declarative classes
- Services: `src/app/services/` — business logic, called by routes
- Tests: `tests/` — mirrors `src/app/` structure

<!-- cxp:section:dependencies -->
## Dependencies and Setup

- Flask for HTTP routing. SQLAlchemy as the ORM with Flask-SQLAlchemy.
- Marshmallow handles request and response schema validation.
- Environment variables managed through python-dotenv.
- All versions pinned in requirements.txt. Do not upgrade without approval.
- Config classes in `src/app/config.py` — select via `FLASK_ENV`.

<!-- cxp:section:error-handling -->
## Error Handling

When modifying or creating error handling:
- Register handlers with `@app.errorhandler` in the factory.
- JSON error responses must include `status`, `message`, `error_code`.
- Log all errors as structured JSON via the `logging` module.
- Catch specific exceptions. Never use bare `except`.
- Validate request payloads before passing to service functions.
- Use `abort()` for expected failures with correct HTTP status.
- Production responses must not include tracebacks or internal paths.

<!-- cxp:section:api-routes -->
## API Patterns

- Blueprints group routes by domain: users, tasks, auth.
- Auth middleware runs at the blueprint level via `before_request`.
- Marshmallow schemas validate all incoming request bodies.
- All routes use `/api/v1/` prefix.
- Status codes: 201 for creation, 204 for deletion, 409 for conflicts.
- Rate-limit public endpoints with Flask-Limiter.
- CORS: explicit allowed origins only, no wildcards.

## Database Rules

- Use Flask-Migrate for all schema changes. No manual DDL.
- One model per file in `src/app/models/`.
- Required columns must have `nullable=False`.
- Index foreign keys and columns used in queries.

## Code Quality

- Type hints on all function signatures. Use `str | None` not `Optional`.
- Google-style docstrings on all public functions.
- Functions under 40 lines. Use guard clauses to reduce nesting.
- When writing tests, use pytest fixtures from `conftest.py`.
- Mirror source structure in test file paths.
