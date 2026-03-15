# GEMINI.md

## About This Project

Internal task management API built with Flask and SQLAlchemy. Python 3.11+.
Deployed on Kubernetes. Uses an application factory pattern.

## When Generating Code

- Always add type hints. Prefer `str | None` over `Optional[str]`.
- Write Google-style docstrings on all public functions and classes.
- Keep functions focused and under 40 lines.
- Prefer functional style over classes unless state management requires it.
- Use descriptive names: `is_valid`, `has_access`, `total_count`.
- Use `pathlib.Path` for any filesystem operations.

## Project Layout

Routes are in `src/app/blueprints/`, one file per domain (users, tasks, auth).
Models live in `src/app/models/` as SQLAlchemy declarative classes.
Business logic belongs in `src/app/services/`, never in route handlers.
Shared utilities and decorators go in `src/app/utils/`.
The application factory is `create_app()` in `src/app/__init__.py`.

<!-- cxp:section:dependencies -->
## Project Dependencies

The API uses Flask for routing and SQLAlchemy via Flask-SQLAlchemy as the ORM.
Marshmallow provides request validation and response serialization. Development
environment variables are managed with python-dotenv. All package versions are
pinned in requirements.txt. Configuration is loaded from environment variables
with fallback defaults in `src/app/config.py`.

<!-- cxp:section:error-handling -->
## Error Handling Approach

The project uses centralized error handling through Flask's `@app.errorhandler`
decorators registered in the application factory. Error responses are JSON with
`status`, `message`, and `error_code` fields. Logging uses structured JSON
output through Python's standard `logging` module.

Guidelines for error handling code:
- Catch specific exception types, never bare `except`.
- Validate all request data at the route boundary.
- Use `abort()` with appropriate HTTP status codes for expected failures.
- Let unexpected exceptions propagate to the centralized handler.
- Never expose tracebacks or internal file paths in responses.

<!-- cxp:section:api-routes -->
## API Structure

Routes are organized with Flask Blueprints, grouped by domain. Authentication
is enforced at the blueprint level using `before_request` hooks rather than
per-route decorators. All endpoints use a `/api/v1/` prefix.

When writing route handlers:
- Validate request payloads with Marshmallow schemas.
- Return 201 for resource creation, 204 for deletion, 409 for conflicts.
- Apply rate limiting on public endpoints using Flask-Limiter.
- Configure CORS with explicit allowed origins, no wildcards.

## Database

Schema migrations are managed with Flask-Migrate (Alembic). Tables are never
modified manually. Models are defined in `src/app/models/`, one file per entity.
Required columns must specify `nullable=False`. Foreign key columns and any
column used in WHERE clauses should be indexed.

## Testing

Tests use pytest with shared fixtures defined in `conftest.py`. A test client
fixture provides an isolated Flask application context. Model instances are
created through factory_boy factories. Test files mirror the source directory
structure: `tests/blueprints/test_users.py` corresponds to
`src/app/blueprints/users.py`.
