# Copilot Instructions

## Language and Framework

This is a Python 3.11+ Flask application. Use type hints on all signatures.
Prefer `str | None` over `Optional[str]`. Follow Google-style docstrings.

## Code Generation Preferences

- Write functions, not classes, unless a class is clearly needed.
- Use guard clauses to avoid deep nesting.
- Keep generated functions under 40 lines.
- Always include type hints on parameters and return values.
- Use `pathlib.Path` for file paths, not `os.path`.
- Name variables descriptively: `is_active`, `has_permission`, `user_count`.
- When generating tests, use pytest with fixtures, not unittest.

## Project Patterns

- Routes live in `src/app/blueprints/`, one file per domain.
- Business logic goes in `src/app/services/`, never in route handlers.
- Models are SQLAlchemy declarative classes in `src/app/models/`.
- Marshmallow schemas handle request validation and response serialization.
- Application factory pattern: `create_app()` in `src/app/__init__.py`.

<!-- cxp:section:dependencies -->
## Dependencies

- Flask for routing, SQLAlchemy as ORM via Flask-SQLAlchemy.
- Marshmallow for schema validation.
- python-dotenv for development environment variables.
- Pin exact versions in requirements.txt.
- Configuration loaded from environment; `src/app/config.py` holds defaults.

<!-- cxp:section:error-handling -->
## Error Handling Patterns

When generating error handling code:
- Use `@app.errorhandler` decorators for HTTP exceptions.
- Return JSON with `status`, `message`, and `error_code` keys.
- Log errors as structured JSON using the `logging` module.
- Catch specific exception types, not bare `except`.
- Call `abort()` with the correct HTTP status for known failures.
- Validate request data at the route level before calling service functions.
- Never include stack traces or file paths in error responses.

<!-- cxp:section:api-routes -->
## API Conventions

When generating route handlers:
- Group routes in Flask Blueprints by domain (users, tasks, auth).
- Apply auth checks at the blueprint level with `before_request`.
- Validate incoming payloads with Marshmallow before processing.
- Prefix all routes with `/api/v1/`.
- Return 201 for creation, 204 for deletion, 409 for conflicts.
- Rate-limit public endpoints using Flask-Limiter.
- Set CORS allowed origins explicitly, never use wildcard.

## Database

- Flask-Migrate for schema changes. Never edit tables by hand.
- One model file per domain in `src/app/models/`.
- Set `nullable=False` on required columns.
- Index columns used in WHERE clauses and JOINs.
- Use `relationship()` for foreign key associations.

## Testing

- pytest as test runner. Shared fixtures in `conftest.py`.
- Test client fixture with isolated Flask app context.
- Use factory_boy for generating model instances.
- Test file names mirror source: `tests/blueprints/test_users.py`.
- Prefer parametrize over duplicated test functions.
